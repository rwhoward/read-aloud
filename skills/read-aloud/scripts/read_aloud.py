"""Build listen-friendly HTML pages for Microsoft Edge's Read Aloud.

Claude writes each part of a document as a small HTML body fragment; this script
wraps the fragments in a consistent page, checks them for things text-to-speech
reads badly, estimates listening time, keeps a library index of everything ever
generated, and opens the result in Edge.

Usage:
  python read_aloud.py init --title "Title" [--source PATH ...] [--project NAME]
  python read_aloud.py build DOC_DIR [--no-open]
  python read_aloud.py library [--open]

Storage root is $READ_ALOUD_HOME, or ~/ReadAloud when that is unset. Documents
land in <root>/<project>/<YYYY-MM-DD>_<slug>/, never inside a project repo.

Plain Python 3, standard library only.
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

WORDS_PER_MINUTE = 155  # Edge Read Aloud natural voices at normal speed
FIDELITY_FLOOR = 0.7    # narration below this fraction of source prose looks like a summary
FIDELITY_CEILING = 3.0  # above this multiple, prompt one look for padding (advisory, not a target)
PROSE_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".adoc", ".html", ".htm", ""}

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
.lib-filter { width: 100%; box-sizing: border-box; font: inherit; font-size: 1rem; padding: .5rem .75rem;
  border: 1px solid var(--rule); border-radius: 6px; background: var(--card); color: var(--fg); margin-bottom: 1rem; }
.lib-tip { color: var(--muted); font-size: 1rem; }
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
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
    """Name of the git repo containing `start`, or 'general' outside any repo."""
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

def cmd_init(args):
    sources = [Path(s).expanduser().resolve() for s in args.source]
    for s in sources:
        if not s.exists():
            sys.exit(f"Source not found: {s}")

    project = slugify(args.project, 40) if args.project else detect_project(sources[0] if sources else Path.cwd())
    if not project:
        existing = sorted(p.name for p in library_root().glob("*") if p.is_dir())
        print("The source isn't in a git repo, so the project can't be detected. Rerun with --project NAME.")
        print("Reuse one of these if the material belongs to it: " + (", ".join(existing) if existing else "(none yet)"))
        print("Otherwise use the folder name of the repo or codebase it's about, or 'general'.")
        sys.exit(3)
    stem = f"{datetime.now():%Y-%m-%d}_{slugify(args.title)}"
    parent = library_root() / project
    doc = parent / stem
    n = 2
    while doc.exists():
        doc = parent / f"{stem}-{n}"
        n += 1

    for sub in ("src", "source", "notes"):
        (doc / sub).mkdir(parents=True)
    for s in sources:
        target = doc / "source" / s.name
        if s.is_dir():
            shutil.copytree(s, target, ignore=shutil.ignore_patterns(".git", "node_modules", "bin", "obj"))
        else:
            shutil.copy2(s, target)

    meta = {
        "title": args.title,
        "project": project,
        "created": datetime.now().isoformat(timespec="seconds"),
        "sources": [str(s) for s in sources],
    }
    (doc / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(doc)
    print(f"\nProject: {project}")
    if not sources:
        print("No source files given. Save the source text verbatim to source/source.md "
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

DISALLOWED_TAGS = re.compile(r"<(pre|code|table|img|svg|script|style|iframe|kbd|samp)\b", re.I)

LONG_PART_WORDS = 1800  # about 12 minutes


def snippet(text, start, end, pad=30):
    a, b = max(0, start - pad), min(len(text), end + pad)
    return re.sub(r"\s+", " ", ("..." if a else "") + text[a:b] + ("..." if b < len(text) else "")).strip()


def lint_part(name, fragment):
    warnings = []

    for tag in sorted({m.group(1).lower() for m in DISALLOWED_TAGS.finditer(fragment)}):
        warnings.append(f"{name}: [<{tag}> in narration] Narrate it instead; exact text belongs in src/reference.html.")

    text = visible_text(fragment)
    for label, rx, advice in CHECKS:
        seen, hits = set(), []
        for m in rx.finditer(text):
            token = m.group(0)
            if token in ALLOW.get(label, ()) or (label == "file name" and token.lower() in FILE_OK):
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


DIAGRAM_LANGS = {"", "text", "txt", "plaintext", "plain", "ascii", "diagram", "mermaid", "dot", "graphviz"}
BOX_DRAWING = re.compile("[─-╿▲-◄←-⇿]")


def source_prose_words(source_dir):
    """Words of prose in the saved source, and how many of those came from diagrams.

    Code blocks are left out because their exact text moves to the reference card. Diagram blocks
    stay in, because diagrams get narrated.
    """
    diagram_words = 0

    def keep_diagrams(m):
        nonlocal diagram_words
        lang, body = m.group(2).strip().lower(), m.group(3)
        if lang in DIAGRAM_LANGS or BOX_DRAWING.search(body):
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


def cmd_build(args):
    doc = Path(args.doc_dir).expanduser().resolve()
    src = doc / "src"
    meta_path = doc / "meta.json"
    if not src.is_dir():
        sys.exit(f"No src/ folder in {doc}. Run init first.")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {
        "title": doc.name.split("_", 1)[-1].replace("-", " ").title(),
        "project": doc.parent.name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "sources": [],
    }
    title = meta["title"]

    part_files = sorted(p for p in src.glob("*.html") if re.match(r"\d", p.name))
    if not part_files:
        sys.exit(f"No parts found. Write src/01.html (and 02.html, ...) in {doc}.")

    errors, warnings, parts = [], [], []
    for path in part_files:
        fragment = path.read_text(encoding="utf-8")
        h1s = re.findall(r"<h1\b[^>]*>(.*?)</h1>", fragment, flags=re.S | re.I)
        if len(h1s) != 1:
            errors.append(f"{path.name}: needs exactly one <h1> (the part title); found {len(h1s)}.")
            continue
        body = re.sub(r"<h1\b[^>]*>.*?</h1>", "", fragment, count=1, flags=re.S | re.I).strip()
        part_title = re.sub(r"\s+", " ", visible_text(h1s[0])).strip()
        words = count_words(visible_text(fragment))
        parts.append({"src": path.name, "title": part_title, "body": body, "words": words})
        warnings += lint_part(path.name, fragment)

    if errors:
        print("Build stopped:\n" + "\n".join(f"  {e}" for e in errors))
        sys.exit(2)

    has_ref = (src / "reference.html").exists()
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
            f"<footer>\n<p class=\"bridge\">{bridge}</p>\n<nav>{' '.join(links)}</nav>\n</footer>"
        )
        page_title = title if single else f"{title} — Part {i} of {total_parts}"
        (doc / part["file"]).write_text(
            PAGE.format(title=html.escape(page_title), fonts=FONT_LINK, css=CSS, content=content), encoding="utf-8")

    if has_ref:
        ref_body = (src / "reference.html").read_text(encoding="utf-8")
        back = " ".join(nav_link(p["file"], f"Part {n}" if not single else "Back to the reading")
                        for n, p in enumerate(parts, 1))
        content = (
            f"<header>\n<p class=\"kicker\">{html.escape(title)}</p>\n<h1>Reference card</h1>\n</header>\n"
            f"<article>\n{ref_body}\n</article>\n<footer>\n<nav>{back}</nav>\n</footer>"
        )
        (doc / "reference.html").write_text(
            PAGE.format(title=html.escape(f"{title} — Reference card"), fonts=FONT_LINK, css=CSS, content=content),
            encoding="utf-8")
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

    if not args.no_open:
        open_in_edge(doc / parts[0]["file"])


# ----------------------------------------------------------------------- library

def write_library():
    root = library_root()
    root.mkdir(parents=True, exist_ok=True)
    docs = []
    for meta_path in root.glob("*/*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not meta.get("parts"):
            continue
        meta["_dir"] = meta_path.parent.relative_to(root).as_posix()
        docs.append(meta)

    docs.sort(key=lambda m: m.get("created", ""), reverse=True)
    projects = {}
    for m in docs:
        projects.setdefault(m.get("project", "general"), []).append(m)

    sep = " · "
    sections = []
    for project, items in projects.items():
        rows = []
        for m in items:
            when = m.get("created", "")[:10]
            n = len(m["parts"])
            bits = [when, plural(n, "part"), f"about {m.get('minutes', '?')} min"]
            ref = f'{sep}<a href="{m["_dir"]}/reference.html">reference card</a>' if m.get("reference") else ""
            rows.append(
                f'<li data-search="{html.escape((m["title"] + " " + project).lower())}">'
                f'<a href="{m["_dir"]}/{m["parts"][0]["file"]}">{html.escape(m["title"])}</a><br>'
                f'<span class="lib-meta">{sep.join(bits)}{ref}</span></li>')
        sections.append(f'<section><h2>{html.escape(project)}</h2>\n<ul>\n' + "\n".join(rows) + "\n</ul></section>")

    body = "\n".join(sections) or "<p>Nothing here yet.</p>"
    content = (
        "<header>\n<h1>Read Aloud library</h1>\n"
        "<p class=\"lib-tip\">Open a document, then press Ctrl+Shift+U in Edge to start Read Aloud. "
        "The voice and speed are in the Read Aloud toolbar.</p>\n</header>\n"
        "<input class=\"lib-filter\" type=\"search\" placeholder=\"Filter by title or project\" "
        "oninput=\"filterLib(this.value)\">\n"
        f"<article id=\"lib\">\n{body}\n</article>\n"
        "<script>function filterLib(q){q=q.toLowerCase();document.querySelectorAll('#lib li').forEach(li=>"
        "{li.hidden=!li.dataset.search.includes(q)});document.querySelectorAll('#lib section').forEach(s=>"
        "{s.hidden=![...s.querySelectorAll('li')].some(li=>!li.hidden)})}</script>"
    )
    (root / "index.html").write_text(
        PAGE.format(title="Read Aloud library", fonts=FONT_LINK, css=CSS, content=content), encoding="utf-8")
    return root / "index.html"


def cmd_library(args):
    index = write_library()
    print(index)
    if args.open:
        open_in_edge(index)


# -------------------------------------------------------------------------- open

def open_in_edge(path):
    url = Path(path).resolve().as_uri()
    candidates = []
    if sys.platform == "win32":
        candidates = [shutil.which("msedge")] + [
            str(Path(os.environ.get(var, "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
            for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
        ]
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
    p.add_argument("--project", help="library folder; defaults to the source's git repo name, and is required outside a repo")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("build", help="render parts, check them, update the library, open in Edge")
    p.add_argument("doc_dir")
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("library", help="rebuild the library index")
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=cmd_library)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
