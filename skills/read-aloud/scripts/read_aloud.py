"""Build listen-friendly HTML pages for Microsoft Edge's Read Aloud.

Claude writes each part of a document as a small HTML body fragment; this script
wraps the fragments in a consistent page, checks them for things text-to-speech
reads badly, estimates listening time, keeps a library index of everything ever
generated, and opens the result in Edge.

Usage:
  python read_aloud.py init --title "Title" [--source PATH ...] [--source-url URL ...] [--project NAME]
  python read_aloud.py build DOC_DIR [--open]
  python read_aloud.py library [--open]
  python read_aloud.py list [--project NAME] [--older-than DAYS] [--archived | --all]
  python read_aloud.py archive ID [ID ...]    hide from the index, keep the files
  python read_aloud.py restore ID [ID ...]    undo an archive
  python read_aloud.py delete ID [ID ...]     move to the Recycle Bin; Windows asks before deleting for good

Storage root is $READ_ALOUD_HOME, or ~/ReadAloud when that is unset. Documents
land in <root>/<project>/<YYYY-MM-DD>_<slug>/, never inside a project repo.

Plain Python 3, standard library only. Windows only.
"""

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

WORDS_PER_MINUTE = 155  # Edge Read Aloud natural voices at normal speed
FIDELITY_FLOOR = 0.7    # narration below this fraction of source prose looks like a summary
FIDELITY_CEILING = 3.0  # above this multiple, prompt one look for padding (advisory, not a target)
PROSE_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".adoc", ".html", ".htm"}

# Fonts degrade to Segoe UI offline; everything else is inline so a page still
# works if its folder is moved or copied elsewhere.
FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible+Next:wght@400;700'
    '&display=swap" rel="stylesheet">'
)

CSS = """
:root { --bg:#faf7f0; --fg:#1f2328; --muted:#5d6370; --accent:#2c5f8f; --rule:#e2dacb; --card:#f2ece0; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1a1c21; --fg:#e7e5e0; --muted:#a3a7b0; --accent:#8fbbe9; --rule:#33363e; --card:#24272e; }
}
html { background: var(--bg); }
body { margin: 0; background: var(--bg); color: var(--fg);
  font-family: "Atkinson Hyperlegible Next", "Atkinson Hyperlegible", "Segoe UI", system-ui, sans-serif;
  font-size: 1.2rem; line-height: 1.75; }
main { max-width: 40rem; margin: 0 auto; padding: 3rem 1rem 5rem; }
.kicker { color: var(--muted); font-size: 1rem; margin: 0 0 .35rem; }
h1 { font-size: 2rem; line-height: 1.25; margin: 0 0 2rem; }
h2 { font-size: 1.45rem; line-height: 1.3; margin: 3rem 0 1rem; padding-top: 1.25rem; border-top: 1px solid var(--rule); }
h3 { font-size: 1.2rem; line-height: 1.35; margin: 2rem 0 .75rem; }
p { margin: 0 0 1.2em; }
ul, ol { padding-left: 1.4em; margin: 0 0 1.2em; }
li { margin: 0 0 .6em; }
blockquote { margin: 1.5rem 0; padding: .8rem 1.2rem; background: var(--card);
  border-left: 4px solid var(--accent); border-radius: 4px; }
blockquote p:last-child { margin-bottom: 0; }
a { color: var(--accent); }
footer { margin-top: 3.5rem; padding-top: 1.5rem; border-top: 1px solid var(--rule); }
footer nav { display: flex; flex-wrap: wrap; gap: .5rem 1.5rem; font-size: 1rem; }
pre { background: var(--card); padding: 1rem; border-radius: 6px; overflow-x: auto; font-size: .85rem; line-height: 1.5; }
code { font-family: "Cascadia Code", Consolas, monospace; font-size: .88em; }
pre code { font-size: inherit; }
table { border-collapse: collapse; width: 100%; font-size: .95rem; margin: 0 0 1.5em; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--rule); padding: .4rem .6rem; text-align: left; vertical-align: top; }
th { background: var(--card); }
.lib-meta { color: var(--muted); font-size: .95rem; }
.lib-parts { font-size: .95rem; color: var(--muted); }
.lib-part { white-space: nowrap; }
.lib-filter { width: 100%; box-sizing: border-box; font: inherit; font-size: 1rem; padding: .5rem .75rem;
  border: 1px solid var(--rule); border-radius: 6px; background: var(--card); color: var(--fg); margin-bottom: 1rem; }
.lib-tip { color: var(--muted); font-size: 1rem; }
figure { margin: 1.5rem 0; padding: .7rem 1rem 1rem; background: var(--card); border-radius: 8px; overflow-x: auto; }
figure::before { content: "Not read aloud"; display: block; font-size: .78rem; letter-spacing: .03em;
  color: var(--muted); margin-bottom: .5rem; }
figure pre, figure table { margin: 0; padding: 0; background: transparent; }
figure img, figure svg { display: block; max-width: 100%; height: auto; margin: 0 auto; }
figure pre.mermaid { text-align: center; font-family: inherit; }
figcaption { font-size: .9rem; color: var(--muted); margin-top: .6rem; }
.sources { margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid var(--rule); font-size: .9rem; color: var(--muted); }
.sources h2 { font-size: 1rem; margin: 0 0 .5rem; padding: 0; border: 0; }
.sources h2::after { content: " \\00b7  not read aloud"; font-weight: normal; font-size: .8rem; }
.sources li { margin-bottom: .4em; overflow-wrap: anywhere; }
.src-ref { font-size: .7em; color: var(--muted); margin-left: .1em; }
.src-ref a { text-decoration: none; }
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{fonts}
<style>{css}</style>
</head>
<body>
<main>
{content}
</main>
</body>
</html>
"""


# Edge Read Aloud skips anything marked aria-hidden="true" and never reads images, not even their alt
# text. It does read collapsed <details> content and unselectable text. (Tested in Edge, 2026-10-02.)
# So visual material stays on the page for the eyes, and the build marks it aria-hidden for the ear.
VISUAL_BLOCK = re.compile(r"<(figure|pre|table|svg)\b.*?</\1\s*>", re.S | re.I)

# Mermaid runs in pages that hold the user's documents, so it's pinned to one release and checked against that
# release's hash. To upgrade, take the new hash from https://data.jsdelivr.com/v1/packages/npm/mermaid@VERSION?structure=flat
MERMAID_SRC = "https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js"
MERMAID_SRI = "sha256-WB7X10vZBI0OOpE2OSfXLvIpQtdyJUayf3zCnjU5Drg="
MERMAID_INIT = ('mermaid.initialize({startOnLoad: true, theme: matchMedia("(prefers-color-scheme: dark)").matches '
                '? "dark" : "neutral"});')

# Sources are untrusted, so anything that could run code or pull in another page stops the build, wherever it
# appears, figures and the reference card included. Each page's Content-Security-Policy backs this up.
UNSAFE_TAG = re.compile(r"<(script|iframe|frame|frameset|object|embed|applet|base|meta|link|form)\b", re.I)
TAG = re.compile(r"""<[a-zA-Z](?:[^>"']|"[^"]*"|'[^']*')*>""")


def hide_visuals(body):
    """Mark every visual block aria-hidden so Read Aloud skips it."""
    return re.sub(r"<(figure|pre|table|svg)\b(?![^>]*\baria-hidden=)", r'<\1 aria-hidden="true"', body, flags=re.I)


def spoken(fragment):
    """The part of a fragment Read Aloud will voice: visual blocks and images removed."""
    return re.sub(r"<img\b[^>]*>", " ", VISUAL_BLOCK.sub(" ", fragment), flags=re.I)


# A source marker in a fragment, such as <sup data-source="1,3"></sup>. The build turns it into small links to the
# sources list, hidden from Read Aloud like everything else that's only for the eyes.
SOURCE_REF = re.compile(r"""<sup\b[^>]*\bdata-source=["']([^"']*)["'][^>]*>\s*</sup>""", re.I)
WEB_ADDRESS = re.compile(r"https?://", re.I)


def doc_sources(doc, meta):
    """The document's sources, numbered from 1: each original init recorded, then any other file saved in source/.
    A saved file with the same stem as a recorded copy, such as the extracted text of a PDF, joins that entry."""
    entries = []
    for item in meta.get("sources", []):
        if isinstance(item, str):  # documents made before sources were listed stored bare paths
            item = {"original": item, "copy": f"source/{Path(item).name}"}
        entries.append(dict(item, extra=[]))
    claimed = {e.get("copy") for e in entries}
    by_stem = {Path(e["copy"]).stem.lower(): e for e in entries if e.get("copy")}
    folder = doc / "source"
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        rel = f"source/{path.name}"
        if rel in claimed:
            continue
        owner = by_stem.get(path.stem.lower())
        if owner is not None:
            owner["extra"].append(rel)
        else:
            entries.append({"original": None, "copy": rel, "extra": []})
    return entries


def sources_section(doc, entries):
    """The numbered list at the bottom of every page, hidden from Read Aloud: each original, where it lives, and the
    saved copy, which is exactly what was narrated even after the original changes or moves."""
    if not entries:
        return ""
    rows = []
    for n, entry in enumerate(entries, 1):
        original, links = entry.get("original"), []
        if original and WEB_ADDRESS.match(original):
            links.append(f'<a href="{html.escape(original)}">{html.escape(original)}</a>')
        elif original:
            path = Path(original)
            if not path.is_absolute() or not path.exists():
                links.append(f"{html.escape(path.name)} (no longer in {html.escape(str(path.parent))})")
            elif path.is_dir():
                links.append(f'<a href="{html.escape(path.as_uri())}/">{html.escape(str(path))}</a>')
            else:
                links.append(f'<a href="{html.escape(path.as_uri())}">{html.escape(path.name)}</a> in '
                             f'<a href="{html.escape(path.parent.as_uri())}/">{html.escape(str(path.parent))}</a>')
        copies = [c for c in [entry.get("copy")] + entry.get("extra", []) if c and (doc / c).exists()]
        for i, copy in enumerate(copies):
            if i:
                label = f"text version, {Path(copy).name}"
            else:
                label = "saved copy" if links else f"{Path(copy).name}, saved copy"
            links.append(f'<a href="{quote(copy)}">{html.escape(label)}</a>')
        rows.append(f'<li id="source-{n}">{" · ".join(links)}</li>')
    return ('<section class="sources" id="sources" aria-hidden="true">\n<h2>Sources</h2>\n<ol>\n'
            + "\n".join(rows) + "\n</ol>\n</section>")


def expand_source_refs(body):
    """Turn each <sup data-source="1,3"></sup> marker into small links to the sources list, hidden from Read Aloud."""
    def links(m):
        nums = [n for n in re.split(r"[\s,]+", m.group(1)) if n]
        return ('<sup class="src-ref" aria-hidden="true">['
                + ", ".join(f'<a href="#source-{html.escape(n)}">{html.escape(n)}</a>' for n in nums) + "]</sup>")
    return SOURCE_REF.sub(links, body)


def unsafe_markup(name, fragment):
    """Errors for markup that could run code in the page: script-like tags, event handlers, javascript: links."""
    found = {f"a <{m.group(1).lower()}> tag" for m in UNSAFE_TAG.finditer(fragment)}
    for tag in TAG.findall(fragment):
        tag = html.unescape(tag)  # browsers decode entities in attribute values, so check what they'll see
        if re.search(r"""[\s/"']on[a-z]+\s*=""", tag, re.I):
            found.add("an event handler such as onerror")
        if re.search(r"""=\s*["']?[\x00-\x20]*(?:javascript|vbscript):""", re.sub(r"[\t\n\r]", "", tag), re.I):
            found.add("a javascript: link")
    return [f"{name}: contains {item}, which could run code in the page. Show markup from a source as escaped "
            "text inside a figure instead." for item in sorted(found)]


def script_hash(code):
    return "'sha256-" + base64.b64encode(hashlib.sha256(code.encode("utf-8")).digest()).decode() + "'"


def render_page(title, content, scripts=()):
    """A full page around `content`, with Mermaid when it has a diagram. The Content-Security-Policy allows no
    other scripts, no images from the web, and no requests, so markup that gets past the checks can't do harm."""
    mermaid = bool(re.search(r"""class=["'][^"']*\bmermaid\b""", content))
    scripts = ([MERMAID_INIT] if mermaid else []) + list(scripts)
    tags = (f'<script src="{MERMAID_SRC}" integrity="{MERMAID_SRI}" crossorigin="anonymous"></script>'
            if mermaid else "") + "".join(f"<script>{s}</script>" for s in scripts)
    allowed = " ".join((["https://cdn.jsdelivr.net"] if mermaid else []) + [script_hash(s) for s in scripts]) or "'none'"
    csp = ("default-src 'none'; img-src 'self' file: data:; style-src 'unsafe-inline' https://fonts.googleapis.com; "
           f"font-src https://fonts.gstatic.com; script-src {allowed}; base-uri 'none'; form-action 'none'")
    return PAGE.format(title=html.escape(title), csp=csp, fonts=FONT_LINK, css=CSS, content=content + tags)


def utf8_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def library_root():
    env = os.environ.get("READ_ALOUD_HOME")
    return Path(env).expanduser() if env else Path.home() / "ReadAloud"


def slugify(text, limit=60):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "document"


def detect_project(start):
    """Name of the git repo containing `start`, or None outside any repo."""
    start = Path(start)
    folder = start if start.is_dir() else start.parent
    try:
        out = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return slugify(Path(out.stdout.strip()).name, 40)
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def visible_text(fragment):
    """Text as text-to-speech would encounter it: tags out, entities decoded."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    text = re.sub(r"</?(p|h[1-6]|li|ul|ol|blockquote|div|br|tr|section|article)\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def count_words(text):
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", text))


def minutes(words):
    return max(1, round(words / WORDS_PER_MINUTE))


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


# --------------------------------------------------------------------------- init

SKIP_DIRS = {"node_modules", "bin", "obj"}  # hidden folders such as .git and .venv are always skipped too
SOURCE_FILE_LIMIT = 300                      # past these, a whole repo was passed rather than its documents
SOURCE_MB_LIMIT = 25


def git_visible_files(folder):
    """Files under `folder` that git tracks or would track, so ignored files never get copied.
    None when the folder isn't in a git repo."""
    try:
        out = subprocess.run(["git", "-C", str(folder), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [folder / p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]


def folder_files(folder):
    """The files in a source folder worth copying: not ignored by git, not hidden, not dependencies or build output."""
    files = git_visible_files(folder)
    if files is None:
        files = []
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
            files += [Path(dirpath, f) for f in filenames]
    return sorted(f for f in files if f.is_file() and not any(
        part.startswith(".") or part in SKIP_DIRS for part in f.relative_to(folder).parts))


def source_plan(sources):
    """Every file to copy, as (original, path under source/), plus notes for the caller to print.
    A source whose name an earlier source already took gets a number, so nothing is overwritten."""
    plan, notes, taken = [], [], set()
    for s in sources:
        name, n = s.name, 2
        while name.lower() in taken:
            name = f"{s.stem}-{n}{s.suffix}" if s.is_file() else f"{s.name}-{n}"
            n += 1
        taken.add(name.lower())
        if name != s.name:
            notes.append(f"{s} is saved as source/{name}, because an earlier source has the same name.")
        if s.is_file():
            plan.append((s, Path(name)))
            if s.suffix.lower() not in PROSE_SUFFIXES:
                notes.append(f"The fidelity check only reads text files, so also save the text of {name} "
                             f"as source/{Path(name).stem}.md.")
            continue
        files = folder_files(s)
        if not files:
            notes.append(f"Nothing was copied from {s}: everything in it is ignored by git, hidden, or build output.")
        plan += [(f, Path(name) / f.relative_to(s)) for f in files]
    return plan, notes


def cmd_init(args):
    sources = [Path(s).expanduser().resolve() for s in args.source]
    for s in sources:
        if not s.exists():
            sys.exit(f"Source not found: {s}")
    for url in args.source_url:  # these become links on every page, so nothing but web addresses
        if not WEB_ADDRESS.match(url):
            sys.exit(f"--source-url needs a web address starting with http:// or https://, not {url!r}. "
                     "Nothing was created.")

    # With no source file there's nothing to detect from. The working folder is often an unrelated repo.
    project = slugify(args.project, 40) if args.project else (detect_project(sources[0]) if sources else None)
    if not project:
        existing = sorted(p.name for p in library_root().glob("*") if p.is_dir())
        reason = "the source isn't in a git repo" if sources else "there's no source file to detect it from"
        print(f"The project can't be detected, because {reason}. Rerun with --project NAME.")
        print("Reuse one of these if the material belongs to it: " + (", ".join(existing) if existing else "(none yet)"))
        print("Otherwise use the folder name of the repo or codebase it's about, or 'general'.")
        sys.exit(3)

    plan, notes = source_plan(sources)
    size = sum(f.stat().st_size for f, _ in plan)
    if len(plan) > SOURCE_FILE_LIMIT or size > SOURCE_MB_LIMIT * 1024 * 1024:
        sys.exit(f"The sources come to {plural(len(plan), 'file')} and {size / 1048576:.1f} MB, more than a document "
                 f"needs. The limit is {SOURCE_FILE_LIMIT} files or {SOURCE_MB_LIMIT} MB. Pass the documents "
                 "themselves, or a folder of documents, rather than a whole repo. Nothing was created.")

    stem = f"{datetime.now():%Y-%m-%d}_{slugify(args.title)}"
    parent = library_root() / project
    doc = parent / stem
    n = 2
    while doc.exists():
        doc = parent / f"{stem}-{n}"
        n += 1

    for sub in ("src", "source", "notes"):
        (doc / sub).mkdir(parents=True)
    for original, saved_as in plan:
        target = doc / "source" / saved_as
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)

    # Record each source with the name it was saved under, which differs when an earlier source had the same name.
    recorded = []
    for s in sources:
        top = next((saved_as.parts[0] for original, saved_as in plan if original == s or s in original.parents), None)
        recorded.append({"original": str(s), "copy": f"source/{top}" if top else None})
    taken = {str(r["copy"]).lower() for r in recorded}
    for url in args.source_url:
        stem, n = slugify(url.split("://", 1)[1], 50), 2
        name = f"source/{stem}.html"
        while name.lower() in taken:
            name, n = f"source/{stem}-{n}.html", n + 1
        taken.add(name.lower())
        recorded.append({"original": url, "copy": name})

    meta = {
        "title": args.title,
        "project": project,
        "created": datetime.now().isoformat(timespec="seconds"),
        "sources": recorded,
    }
    (doc / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(doc)
    print(f"\nProject: {project}")
    for note in notes:
        print(note)
    if recorded:
        print("Sources, numbered for source markers:")
        for n, entry in enumerate(recorded, 1):
            print(f"  {n}. {entry['original']}")
    for entry in recorded[len(sources):]:
        print(f'Download it with: curl.exe -sL -o "{doc / entry["copy"]}" "{entry["original"]}"')
    if not recorded:
        print("No source files given. Save the source to source/, word for word, "
              "so the fidelity check has something to compare against.")
    print("Write parts to src/01.html, src/02.html, ... and an optional src/reference.html, then run build.")


# -------------------------------------------------------------------------- lint

# Mixed-case words that are ordinary product names, not code identifiers.
NAME_OK = {
    "iOS", "macOS", "iPhone", "iPad", "iPadOS", "eBay", "iCloud", "watchOS", "tvOS", "eSIM", "mRNA",
    "GitHub", "GitLab", "BitBucket", "JavaScript", "TypeScript", "CoffeeScript", "PowerShell", "NuGet",
    "PostgreSQL", "MySQL", "MongoDB", "DevOps", "SharePoint", "OneDrive", "OneNote", "PowerPoint",
    "PowerApps", "PowerBI", "YouTube", "LinkedIn", "WordPress", "OpenAI", "WebSocket", "WebSockets",
    "BigQuery", "PagerDuty", "ClickUp", "HubSpot", "QuickBooks", "MacBook", "ServiceNow", "NetSuite",
    "DocuSign", "SendGrid", "MailChimp", "TeamCity", "SonarQube", "SonarCloud", "ReSharper", "WinForms",
    "VirtualBox", "FedEx", "LaunchDarkly", "DataDog", "CloudFlare", "WebAssembly",
    "GraphQL", "OAuth", "FastAPI", "AppVeyor", "SignalR", "IntelliSense", "FreeBSD",
}
FILE_OK = {"node.js", "vue.js", "next.js", "nuxt.js", "express.js", "three.js", "d3.js", "chart.js", "asp.net"}

FILE_EXT = (r"cs|csproj|sln|vb|fs|ts|tsx|js|jsx|mjs|cjs|py|ipynb|json|ya?ml|md|xml|config|sql|sh|bash|ps1|psm1|"
            r"tf|tfvars|tfbackend|bicep|toml|ini|env|html?|css|scss|go|rs|java|kt|rb|php|txt|lock|dll|exe|"
            r"razor|cshtml|vue|svelte|dockerfile|csv|xlsx|log")

CHECKS = [
    ("markdown left over", re.compile(r"\*\*|__|`|^\s*#{1,6}\s|\]\(|^\s*[-*+]\s", re.M),
     "Markdown syntax gets read out literally or not at all. Convert it to HTML or words."),
    ("URL", re.compile(r"https?://\S+|www\.\S+"),
     "Never make the listener hear a URL. Describe where it points; put the link on the reference card."),
    ("emoji", re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]"),
     "Emoji get read as their names (\"check mark button\"). Say what they meant in words."),
    ("arrow or operator", re.compile(r"->|=>|<-|[←-⇿]|&&|\|\||!=|==|>=|<=|\s[<>]\s"),
     "Say it in words: then, leads to, returns, and, or, isn't, at least, more than."),
    ("abbreviation", re.compile(r"\b(?:e\.g|i\.e|etc|vs|approx|cf)\.|\bvs\b|\bw/o?\b", re.I),
     "Write it out: for example, that is, and so on, versus, about, with, without."),
    ("slash", re.compile(r"\b\w+/\w+|(?<![\w.])/[A-Za-z]\w*"),
     "Slashes are read as \"slash\". Use and or or, describe the path, or name the switch in words."),
    ("file name", re.compile(r"\b[\w.-]+\.(?:" + FILE_EXT + r")\b", re.I),
     "Read as \"name dot extension\". Say \"the token store file\" or \"the complexity JSON file\" instead."),
    ("snake_case", re.compile(r"\b[A-Za-z0-9]+_[A-Za-z0-9_]+\b"),
     "Underscores are read as \"underscore\". Write the identifier as spaced words."),
    ("camelCase", re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b"),
     "Run-together identifiers often come out garbled. Write them as spaced words."),
    ("PascalCase", re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b"),
     "Looks like a code identifier. Write it as spaced words. Ignore this for product or brand names."),
    ("ID code", re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]{2,})+\b"),
     "Finding IDs, ticket numbers, and similar codes are tedious to hear. Leave them on the reference card."),
    ("glued unit", re.compile(r"\b(?!(?:1[89]|20)\d0s\b)\d+(?:\.\d+)?(?:ms|ns|us|s|m|h|hrs?|mins?|secs?|kb|mb|gb|tb|KB|MB|GB|TB|k|K|x|X|px)\b"),
     "Spell the unit out: 200 milliseconds, 5 times, 10 thousand, 30 seconds."),
    ("trailing plus", re.compile(r"\b\d+\+"),
     "Write \"or later\" or \"or more\"."),
    ("tilde", re.compile(r"~|≈"),
     "Write \"about\" or \"roughly\"."),
    ("code token", re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b|\b[CF]#"),
     "Write to-do, fix-me, C sharp, F sharp."),
    ("code symbol", re.compile(r"[{}\[\]]|<\w[^>]*>|\$[A-Za-z_]|(?<!\w)#\w|(?<!\w)@\w|\b\w+\(\)"),
     "Code punctuation doesn't survive speech. Name the thing in words; exact text goes on the reference card."),
]

ALLOW = {"camelCase": NAME_OK, "PascalCase": NAME_OK}

DISALLOWED_TAGS = re.compile(r"<(code|details|style|kbd|samp)\b", re.I)  # scripts and iframes stop the build
TAG_ADVICE = {
    "code": "Inline code gets read aloud as-is. Say it in words, or show the exact text in a <figure>.",
    "details": "Read Aloud reads collapsed sections anyway. Put visual material in a <figure> instead.",
}

LONG_PART_WORDS = 1800  # about 12 minutes


def snippet(text, start, end, pad=30):
    a, b = max(0, start - pad), min(len(text), end + pad)
    return re.sub(r"\s+", " ", ("..." if a else "") + text[a:b] + ("..." if b < len(text) else "")).strip()


def lint_part(name, fragment, title_words=frozenset()):
    warnings = []

    unwrapped = re.sub(r"<figure\b.*?</figure\s*>", " ", fragment, flags=re.S | re.I)
    outside = sorted({t.lower() for t in re.findall(r"<(pre|table|svg|img)\b", unwrapped, flags=re.I)})
    if outside:
        warnings.append(f"{name}: [outside a figure] {', '.join(f'<{t}>' for t in outside)} isn't inside a <figure>, "
                        "so Read Aloud skips it without the \"Not read aloud\" label. Wrap it in a <figure>.")

    fragment = spoken(fragment)  # figures, code blocks, tables and charts are hidden from Read Aloud
    for tag in sorted({m.group(1).lower() for m in DISALLOWED_TAGS.finditer(fragment)}):
        advice = TAG_ADVICE.get(tag, "This doesn't belong in a narration part.")
        warnings.append(f"{name}: [<{tag}> in narration] {advice}")

    text = visible_text(fragment)
    for label, rx, advice in CHECKS:
        seen, hits = set(), []
        for m in rx.finditer(text):
            token = m.group(0)
            if token in ALLOW.get(label, ()) or (label == "file name" and token.lower() in FILE_OK) \
                    or (label in ALLOW and token in title_words):  # names in the title are the doc's own subject
                continue
            if token not in seen:  # one example per distinct offender keeps the report readable
                seen.add(token)
                hits.append(m)
        if not hits:
            continue
        shown = "; ".join(f"\"{snippet(text, m.start(), m.end())}\"" for m in hits[:4])
        more = f" (+{len(hits) - 4} more)" if len(hits) > 4 else ""
        warnings.append(f"{name}: [{label}] {advice}\n    {shown}{more}")

    for para in re.findall(r"<p\b[^>]*>(.*?)</p>", fragment, flags=re.S | re.I):
        ptext = visible_text(para)
        words = count_words(ptext)
        if words > 130:
            warnings.append(f"{name}: [long paragraph] {words} words. Split it so each paragraph carries one idea.\n"
                            f"    \"{snippet(ptext, 0, 60, 0)}\"")
        for sentence in re.split(r"(?<=[.!?])\s+", ptext.strip()):
            if count_words(sentence) > 35:
                warnings.append(f"{name}: [long sentence] {count_words(sentence)} words. Break it up.\n"
                                f"    \"{snippet(sentence, 0, 70, 0)}\"")
        for m in re.finditer(r"\(([^)]{60,})\)", ptext):
            warnings.append(f"{name}: [long parenthetical] Parentheses aren't voiced, so the aside blends into "
                            f"the sentence. Make it its own sentence.\n    \"{snippet(ptext, m.start(), m.end(), 0)}\"")

    total = count_words(text)
    if total > LONG_PART_WORDS:
        warnings.append(f"{name}: [long part] {total:,} words, about {minutes(total)} minutes. "
                        "Split it at a topic boundary; aim for 800 to 1,500 words.")
    list_words = sum(count_words(visible_text(li)) for li in re.findall(r"<li\b[^>]*>(.*?)</li>", fragment, flags=re.S | re.I))
    if total and list_words / total > 0.35:
        warnings.append(f"{name}: [list-heavy] {round(100 * list_words / total)}% of this part is list items. "
                        "Keep lists for real steps or options; turn the rest into connected paragraphs.")
    return warnings


DIAGRAM_LANGS = {"ascii", "diagram", "mermaid", "dot", "graphviz"}
PLAIN_LANGS = {"", "text", "txt", "plaintext", "plain"}
BOX_DRAWING = re.compile("[─-╿▲-◄←-⇿]")
ASCII_ART = re.compile(r"[-=]{2,}>|<[-=]{2,}|\+-{2,}")  # arrows like --> and <==, and box corners like +--


def source_prose_words(source_dir):
    """Words of prose in the saved source, and how many of those came from diagrams.

    Code blocks are left out because their exact text moves to a figure or the reference card. Diagram
    blocks stay in, because diagrams get narrated. A block is a diagram when its language says so, when
    it has box-drawing characters, or when it's untagged plain text with ASCII arrows or box corners.
    Untagged blocks without those usually hold commands or logs, so they're left out like code.
    """
    diagram_words = 0

    def keep_diagrams(m):
        nonlocal diagram_words
        lang, body = m.group(2).strip().lower(), m.group(3)
        if lang in DIAGRAM_LANGS or BOX_DRAWING.search(body) or (lang in PLAIN_LANGS and ASCII_ART.search(body)):
            diagram_words += count_words(body)
            return body
        return " "

    total = 0
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in PROSE_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = re.sub(r"^(```|~~~)([^\n]*)\n(.*?)^\1\s*$", keep_diagrams, text, flags=re.S | re.M)
        text = re.sub(r"<pre\b.*?</pre>", " ", text, flags=re.S | re.I)
        if path.suffix.lower() in {".html", ".htm"}:
            text = visible_text(text)
        total += count_words(text)
    return total, diagram_words


# ------------------------------------------------------------------------- build

def nav_link(href, label):
    return f'<a href="{href}">{html.escape(label)}</a>'


def part_number(path):
    return int(re.match(r"\d+", path.name).group())


def cmd_build(args):
    doc = Path(args.doc_dir).expanduser().resolve()
    src = doc / "src"
    meta_path = doc / "meta.json"
    # Build deletes and rewrites files, so like delete, it only touches document folders inside the library.
    if doc.parent.parent != library_root().resolve() or not meta_path.exists():
        sys.exit(f"Refusing to build {doc}: it isn't a document folder that init made inside the library. "
                 "Nothing was changed.")
    if not src.is_dir():
        sys.exit(f"No src/ folder in {doc}. Run init first.")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        title = meta["title"]
    except (ValueError, KeyError, TypeError) as e:
        sys.exit(f"Can't read the title from {meta_path} ({e!r}). Fix that file, then rebuild. Nothing was changed.")

    part_files = sorted((p for p in src.glob("*.html") if re.match(r"\d", p.name)), key=part_number)  # 2 before 10
    if not part_files:
        sys.exit(f"No parts found. Write src/01.html (and 02.html, ...) in {doc}.")

    has_ref = (src / "reference.html").exists()
    ref_body = (src / "reference.html").read_text(encoding="utf-8") if has_ref else ""
    sources = doc_sources(doc, meta)
    marked = False
    errors, warnings, parts = unsafe_markup("reference.html", ref_body), [], []
    numbers = [part_number(p) for p in part_files]
    for n in sorted({n for n in numbers if numbers.count(n) > 1}):
        names = ", ".join(p.name for p in part_files if part_number(p) == n)
        errors.append(f"part {n} has more than one file ({names}). Delete or renumber the stale one.")
    for path in part_files:
        fragment = path.read_text(encoding="utf-8")
        errors += unsafe_markup(path.name, fragment)
        h1s = re.findall(r"<h1\b[^>]*>(.*?)</h1>", fragment, flags=re.S | re.I)
        if len(h1s) != 1:
            errors.append(f"{path.name}: needs exactly one <h1> (the part title); found {len(h1s)}.")
            continue
        body = hide_visuals(re.sub(r"<h1\b[^>]*>.*?</h1>", "", fragment, count=1, flags=re.S | re.I).strip())
        body = expand_source_refs(body)
        for m in SOURCE_REF.finditer(fragment):
            marked = True
            bad = [n for n in re.split(r"[\s,]+", m.group(1)) if n and not (n.isdigit() and 1 <= int(n) <= len(sources))]
            if bad:
                warnings.append(f"{path.name}: [unknown source] A source marker names {', '.join(bad)}, but the "
                                f"sources are numbered 1 to {len(sources)}.")
        part_title = re.sub(r"\s+", " ", visible_text(h1s[0])).strip()
        words = count_words(visible_text(spoken(fragment)))
        parts.append({"src": path.name, "title": part_title, "body": body, "words": words})
        warnings += lint_part(path.name, fragment, frozenset(re.findall(r"\w+", title)))

        said = visible_text(spoken(fragment))
        if not has_ref and re.search(r"reference card", said, re.I):
            warnings.append(f"{path.name}: [stale pointer] The narration mentions the reference card, but there "
                            "isn't one. Point to a figure instead, or write src/reference.html.")
        if re.search(r"\b(?:shown below|on screen)\b", said, re.I) and not VISUAL_BLOCK.search(fragment) \
                and not re.search(r"<img\b", fragment, re.I):
            warnings.append(f"{path.name}: [missing figure] The narration says something is shown below or on "
                            "screen, but this part has no figure.")

    if errors:
        print("Build stopped:\n" + "\n".join(f"  {e}" for e in errors))
        sys.exit(2)

    if len(sources) > 1 and not marked:
        warnings.append(f"[no source markers] This document draws on {len(sources)} sources but never says which "
                        "part came from which. Add <sup data-source=\"N\"></sup> markers.")
    sources_html = sources_section(doc, sources)
    if has_ref and re.search(r"<h1\b", ref_body, re.I):
        warnings.append("reference.html: [duplicate title] The build adds the \"Reference card\" heading. "
                        "Start the card with an <h2>.")
    total_parts = len(parts)
    single = total_parts == 1

    for old in doc.glob("part-*.html"):
        old.unlink()

    for i, part in enumerate(parts, 1):
        part["file"] = f"part-{i}.html"
        part["minutes"] = minutes(part["words"])

    for i, part in enumerate(parts, 1):
        if single:
            kicker = f"About {plural(part['minutes'], 'minute')}."
            if part["title"].strip().lower() != title.strip().lower():
                kicker = f"{html.escape(title)}. {kicker}"
        else:
            kicker = f"{html.escape(title)}. Part {i} of {total_parts}. About {plural(part['minutes'], 'minute')}."

        if i < total_parts:
            nxt = parts[i]
            bridge = (f"That's the end of part {i}. Next up is "
                      f"<a href=\"{nxt['file']}\">part {i + 1}: {html.escape(nxt['title'])}</a>.")
        elif single:
            bridge = "That's the end."
        else:
            bridge = f"That's the end of part {i}, the last part."

        links = []
        if i > 1:
            links.append(nav_link(parts[i - 2]["file"], f"Previous: part {i - 1}"))
        if has_ref:
            links.append(nav_link("reference.html", "Reference card"))
        links.append(nav_link(os.path.relpath(library_root() / "index.html", doc).replace(os.sep, "/"), "Library"))

        content = (
            f"<header>\n<p class=\"kicker\">{kicker}</p>\n<h1>{html.escape(part['title'])}</h1>\n</header>\n"
            f"<article>\n{part['body']}\n</article>\n"
            f"<footer>\n<p class=\"bridge\">{bridge}</p>\n<nav aria-hidden=\"true\">{' '.join(links)}</nav>\n</footer>\n"
            f"{sources_html}"
        )
        page_title = title if single else f"{title} — Part {i} of {total_parts}"
        (doc / part["file"]).write_text(render_page(page_title, content), encoding="utf-8")

    if has_ref:
        back = " ".join(nav_link(p["file"], f"Part {n}" if not single else "Back to the reading")
                        for n, p in enumerate(parts, 1))
        content = (
            f"<header>\n<p class=\"kicker\">{html.escape(title)}</p>\n<h1>Reference card</h1>\n</header>\n"
            f"<article>\n{expand_source_refs(ref_body)}\n</article>\n<footer>\n<nav>{back}</nav>\n</footer>\n{sources_html}"
        )
        (doc / "reference.html").write_text(render_page(f"{title} — Reference card", content), encoding="utf-8")
    elif (doc / "reference.html").exists():
        (doc / "reference.html").unlink()

    narration = sum(p["words"] for p in parts)
    total_minutes = sum(p["minutes"] for p in parts)  # matches the per-part figures the pages show
    source_words, diagram_words = source_prose_words(doc / "source") if (doc / "source").is_dir() else (0, 0)

    meta.update({
        "built": datetime.now().isoformat(timespec="seconds"),
        "parts": [{k: p[k] for k in ("file", "title", "words", "minutes")} for p in parts],
        "words": narration,
        "minutes": total_minutes,
        "reference": has_ref,
        "source_prose_words": source_words,
    })
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    write_library()

    print(f"Built {plural(total_parts, 'part')}, about {plural(total_minutes, 'minute')} "
          f"of listening ({narration:,} words){' plus a reference card' if has_ref else ''}.")
    for p in parts:
        print(f"  {p['file']}  {p['minutes']:>2} min  {p['title']}")
    print(f"\nFolder: {doc}")

    if source_words >= 200:
        ratio = narration / source_words
        print(f"Fidelity: narration is {ratio:.0%} of the source prose ({narration:,} vs {source_words:,} words).")
        if ratio < FIDELITY_FLOOR:
            warnings.insert(0, (
                f"[possible summary] Narration is only {ratio:.0%} of the source prose. Lists and tables usually "
                "expand when spoken, so a big shrink normally means material was summarized away rather than "
                "translated. Re-check the inventory. If the gap is boilerplate you dropped or a lookup table you "
                "moved to the reference card, that's fine."))
        elif ratio > FIDELITY_CEILING:
            print(f"Note: that's above {FIDELITY_CEILING:.0f} times the source, so take one pass for padding: "
                  "definitions of everyday developer terms, a bridge after every short section, restated "
                  "findings that could point back to where they were covered, and a recap longer than a few "
                  "short paragraphs. If none of those apply, leave it. This is a prompt to look, not a number "
                  "to hit."
                  + (" Most of this source is diagrams, and a few labels become a whole narrated tour, so a "
                     "high ratio is expected here." if diagram_words > source_words * 0.3 else ""))
    elif not source_words:
        print("Fidelity: skipped, because no source prose is saved in source/.")
    else:
        print(f"Fidelity: skipped, because the source has only {source_words} words of prose outside code blocks.")

    if warnings:
        print(f"\n{len(warnings)} warning{'s' if len(warnings) != 1 else ''}. Fix what's real, then rebuild:")
        for w in warnings:
            print(f"  {w}")
    else:
        print("\nNo speakability warnings.")

    if args.open:
        open_in_edge(doc / parts[0]["file"])


# ----------------------------------------------------------------------- library

def load_docs():
    """Every document in the library, newest first, with its folder and id attached."""
    root = library_root()
    docs = []
    for meta_path in root.glob("*/*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        meta["_path"] = meta_path.parent
        meta["_id"] = meta_path.parent.relative_to(root).as_posix()
        docs.append(meta)
    docs.sort(key=lambda m: m.get("created", ""), reverse=True)
    return docs


LIBRARY_FILTER = (
    "document.getElementById('lib-filter').addEventListener('input',e=>{const q=e.target.value.toLowerCase();"
    "document.querySelectorAll('#lib li').forEach(li=>{li.hidden=!li.dataset.search.includes(q)});"
    "document.querySelectorAll('#lib section').forEach(s=>{s.hidden=![...s.querySelectorAll('li')]"
    ".some(li=>!li.hidden)})});"
)


def write_library():
    root = library_root()
    root.mkdir(parents=True, exist_ok=True)
    every = load_docs()
    docs = [m for m in every if m.get("parts") and not m.get("archived")]
    archived = sum(1 for m in every if m.get("archived"))

    projects = {}
    for m in docs:
        projects.setdefault(m.get("project", "general"), []).append(m)

    sep = " · "
    sections = []
    for project, items in projects.items():
        rows = []
        for m in items:
            d, parts = m["_id"], m["parts"]
            # Jump links so a returning listener can pick up at the part they stopped on.
            jump = "".join(
                f'{sep}<a class="lib-part" href="{d}/{p["file"]}" title="{html.escape(p["title"])}">part {n}</a>'
                for n, p in enumerate(parts, 1) if n > 1)
            bits = [m.get("created", "")[:10], plural(len(parts), "part"), f"about {m.get('minutes', '?')} min"]
            ref = f'{sep}<a href="{d}/reference.html">reference card</a>' if m.get("reference") else ""
            rows.append(
                f'<li data-search="{html.escape((m["title"] + " " + project).lower())}">'
                f'<a href="{d}/{parts[0]["file"]}" title="{html.escape(parts[0]["title"])}">{html.escape(m["title"])}</a>'
                f'<span class="lib-parts">{jump}</span><br>'
                f'<span class="lib-meta">{sep.join(bits)}{ref}</span></li>')
        sections.append(f'<section><h2>{html.escape(project)}</h2>\n<ul>\n' + "\n".join(rows) + "\n</ul></section>")

    body = "\n".join(sections) or "<p>Nothing here yet.</p>"
    hidden = (f'<p class="lib-tip">{plural(archived, "archived document")} not shown. '
              f'Ask Claude to restore {"it" if archived == 1 else "them"}.</p>' if archived else "")
    content = (
        "<header>\n<h1>Read Aloud library</h1>\n"
        "<p class=\"lib-tip\">Open a document, then press Ctrl+Shift+U in Edge to start Read Aloud. "
        "The voice and speed are in the Read Aloud toolbar.</p>\n</header>\n"
        "<input class=\"lib-filter\" id=\"lib-filter\" type=\"search\" placeholder=\"Filter by title or project\">\n"
        f"<article id=\"lib\">\n{body}\n</article>\n{hidden}\n"
    )
    (root / "index.html").write_text(render_page("Read Aloud library", content, [LIBRARY_FILTER]), encoding="utf-8")
    return root / "index.html"


def cmd_library(args):
    index = write_library()
    print(index)
    if args.open:
        open_in_edge(index)


# ----------------------------------------------------------------------- cleanup

def folder_size(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def resolve_docs(ids):
    """Match ids printed by `list` (project/folder), bare folder names, or full paths to documents."""
    docs = load_docs()
    found = []
    for raw in ids:
        target = Path(raw).expanduser()
        matches = [m for m in docs if raw in (m["_id"], m["_path"].name)
                   or (target.is_absolute() and target.resolve() == m["_path"].resolve())]
        if len(matches) != 1:
            sys.exit(f"{'No document' if not matches else 'More than one document'} matches {raw!r}. "
                     "Use an id from the list command. Nothing was changed.")
        found.append(matches[0])
    return found


def cmd_list(args):
    docs = load_docs()
    if args.project:
        docs = [m for m in docs if m.get("project") == args.project]
    if args.older_than is not None:
        cutoff = (datetime.now() - timedelta(days=args.older_than)).isoformat(timespec="seconds")
        docs = [m for m in docs if m.get("created", "") < cutoff]
    if not args.all:
        docs = [m for m in docs if bool(m.get("archived")) == args.archived]
    if not docs:
        print("No matching documents.")
        return
    for m in docs:
        parts = m.get("parts") or []
        status = "  [archived]" if m.get("archived") else ""
        print(f"{m['_id']}{status}")
        print(f"    {m.get('title', '?')} · {m.get('created', '')[:10]} · {plural(len(parts), 'part')}, "
              f"about {m.get('minutes', '?')} min · {folder_size(m['_path']) / 1024:,.0f} KB")


def set_archived(docs, archived):
    for m in docs:
        path = m["_path"] / "meta.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        if archived:
            meta["archived"] = datetime.now().isoformat(timespec="seconds")
        else:
            meta.pop("archived", None)
        path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"{'Archived' if archived else 'Restored'}: {m['_id']} ({m.get('title', '?')})")
    write_library()


def cmd_archive(args):
    set_archived(resolve_docs(args.ids), True)
    print("Removed from the library index. The files are kept, so restore brings them back.")


def cmd_restore(args):
    set_archived(resolve_docs(args.ids), False)
    print("Back in the library index.")


def send_to_trash(path):
    """Move a folder to the OS trash, so a cleanup can be undone. When Windows can't recycle the folder, it asks
    the user before deleting it permanently, and this can't tell afterwards which of the two happened."""
    path = Path(path).resolve()
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8  # matches shellapi.h
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort),
                        ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                        ("lpszProgressTitle", wintypes.LPCWSTR)]

        # Recycle rather than delete, without prompts, but warn instead of silently destroying the
        # folder if it can't be recycled: ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI | WANTNUKEWARNING.
        flags = 0x0040 | 0x0010 | 0x0004 | 0x0400 | 0x4000
        op = SHFILEOPSTRUCTW(None, 3, str(path) + "\0", None, flags, False, None, None)  # 3 = FO_DELETE
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if result or op.fAnyOperationsAborted or path.exists():
            raise OSError(f"the Recycle Bin move failed with code {result}")
    elif sys.platform == "darwin":
        trash = Path.home() / ".Trash"
        target, n = trash / path.name, 2
        while target.exists():
            target, n = trash / f"{path.name} {n}", n + 1
        shutil.move(str(path), str(target))
    elif shutil.which("gio"):
        subprocess.run(["gio", "trash", str(path)], check=True)
    else:
        raise OSError("no trash is available on this system")


def cmd_delete(args):
    root = library_root().resolve()
    docs = resolve_docs(args.ids)
    for m in docs:
        path = m["_path"].resolve()
        if path.parent.parent != root or not (path / "meta.json").exists():
            sys.exit(f"Refusing to touch {path}: it isn't a document folder inside the library. Nothing was changed.")
    failed = False
    for m in docs:
        try:
            send_to_trash(m["_path"])
        except (OSError, subprocess.SubprocessError) as e:
            print(f"Not deleted: {m['_id']} ({e}). Its files are untouched.")
            failed = True
            continue
        print(f"Deleted: {m['_id']} ({m.get('title', '?')}). It's in the Recycle Bin, unless Windows said it "
              "couldn't recycle it and asked to delete it permanently, and that was approved.")
        project_dir = m["_path"].parent
        if project_dir.exists() and not any(project_dir.iterdir()):
            project_dir.rmdir()
    write_library()
    if failed:
        sys.exit(1)


# -------------------------------------------------------------------------- open

def open_in_edge(path):
    url = Path(path).resolve().as_uri()
    candidates = []
    if sys.platform == "win32":
        # Only Edge's install folders. A PATH lookup on Windows also searches the current folder, where a repo
        # could plant its own msedge, and an unset variable would make the path relative to that folder too.
        roots = [os.environ.get(var, "") for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        candidates = [str(Path(r, "Microsoft", "Edge", "Application", "msedge.exe"))
                      for r in roots if Path(r).is_absolute()]
    elif sys.platform == "darwin":
        candidates = ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        candidates = [shutil.which("microsoft-edge"), shutil.which("microsoft-edge-stable")]

    for exe in candidates:
        if exe and Path(exe).exists():
            subprocess.Popen([exe, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"Opened in Edge: {url}")
            return
    import webbrowser
    webbrowser.open(url)
    print(f"Edge not found; opened in the default browser: {url}")


def main():
    utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create a document folder in the library")
    p.add_argument("--title", required=True)
    p.add_argument("--source", action="append", default=[], help="file or folder to copy in (repeatable)")
    p.add_argument("--source-url", action="append", default=[], help="web page the document comes from (repeatable)")
    p.add_argument("--project", help="library folder; defaults to the source's git repo name, and is required outside a repo")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("build", help="render parts, check them, and update the library")
    p.add_argument("doc_dir")
    p.add_argument("--open", action="store_true", help="open part 1 in Edge afterwards")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("library", help="rebuild the library index")
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=cmd_library)

    p = sub.add_parser("list", help="list documents with their ids, for cleanup")
    p.add_argument("--project", help="only this project")
    p.add_argument("--older-than", type=int, metavar="DAYS", help="only documents created more than DAYS ago")
    which = p.add_mutually_exclusive_group()
    which.add_argument("--archived", action="store_true", help="show archived documents instead of active ones")
    which.add_argument("--all", action="store_true", help="show active and archived documents")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("archive", help="remove documents from the library index, keeping their files")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("restore", help="put archived documents back in the library index")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("delete", help="move documents to the Recycle Bin and drop them from the index")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
