"""Tests for read_aloud.py. Run from the repo root: python -m unittest discover -s tests

Every test works in a temporary library. None of them opens Edge or touches the Recycle Bin.
"""

import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True  # keep __pycache__ out of the installed skill folder
sys.path.insert(0, str(REPO / "skills" / "read-aloud" / "scripts"))
import read_aloud as ra  # noqa: E402

REAL_OPEN_IN_EDGE = ra.open_in_edge


def run(*argv):
    """Run the command line in-process. Returns the exit code and everything it printed."""
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["read_aloud.py", *argv]), redirect_stdout(out), redirect_stderr(out):
        try:
            ra.main()
            code = 0
        except SystemExit as e:
            code = 0 if e.code is None else e.code if isinstance(e.code, int) else 1
            if isinstance(e.code, str):
                out.write(e.code)
    return code, out.getvalue()


def part(title, body="<p>Plain words.</p>"):
    return f"<h1>{title}</h1>\n{body}\n"


class LibraryTest(unittest.TestCase):
    """Each test gets its own library. Edge is never opened, and delete removes folders instead of recycling them."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.lib = self.tmp / "lib"
        env = {"READ_ALOUD_HOME": str(self.lib), "GIT_CEILING_DIRECTORIES": str(self.tmp)}
        for patch in (mock.patch.dict(os.environ, env),
                      mock.patch.object(ra, "open_in_edge"),
                      mock.patch.object(ra, "send_to_trash", side_effect=shutil.rmtree)):
            patch.start()
            self.addCleanup(patch.stop)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def new_doc(self, *sources):
        code, out = run("init", "--project", "test", "--title", "Test doc",
                        *[arg for s in sources for arg in ("--source", str(s))])
        self.assertEqual(code, 0, out)
        return Path(out.splitlines()[0])

    def copied(self, doc):
        return sorted(p.relative_to(doc / "source").as_posix() for p in (doc / "source").rglob("*") if p.is_file())


class SafetyChecks(unittest.TestCase):
    def test_markup_that_could_run_code_is_caught(self):
        for markup in ['<script>alert(1)</script>',
                       '<figure><img src=x onerror="alert(1)"></figure>',
                       '<svg/onload=alert(1)>',
                       '<img src="x"onerror=alert(1)>',
                       '<img alt="a>b" onerror=alert(1)>',
                       '<a href="javascript:alert(1)">x</a>',
                       '<a href="java&#x09;script:alert(1)">x</a>',
                       '<a href=" javascript:alert(1)">x</a>',
                       '<iframe src="https://example.com"></iframe>',
                       '<meta http-equiv="refresh" content="0;url=https://example.com">',
                       '<base href="https://example.com/">']:
            with self.subTest(markup=markup):
                self.assertTrue(ra.unsafe_markup("x", markup))

    def test_escaped_code_and_ordinary_text_pass(self):
        for markup in ['<figure><pre><code>&lt;script&gt;alert(1)&lt;/script&gt;</code></pre></figure>',
                       '<figure><pre><code>button.onclick = handler;\nonline = true;</code></pre></figure>',
                       '<p>The service goes online when x &lt; y.</p>',
                       '<p><a href="https://example.com/docs">the docs</a></p>',
                       '<figure><pre class="mermaid">graph TD; A-->B</pre></figure>']:
            with self.subTest(markup=markup):
                self.assertEqual(ra.unsafe_markup("x", markup), [])

    def test_canonical_examples_pass_every_check(self):
        text = (REPO / "skills" / "read-aloud" / "references" / "examples.md").read_text(encoding="utf-8")
        blocks = re.findall(r"^```html\n(.*?)^```", text, flags=re.S | re.M)
        self.assertGreaterEqual(len(blocks), 8)
        for i, block in enumerate(blocks, 1):
            with self.subTest(example=i):
                self.assertEqual(ra.lint_part(f"example {i}", block), [])
                self.assertEqual(ra.unsafe_markup(f"example {i}", block), [])


class Build(LibraryTest):
    def test_parts_sort_by_number(self):
        doc = self.new_doc()
        for n in range(1, 11):
            self.write(doc / "src" / f"{n}.html", part(f"Section {n}"))
        code, out = run("build", str(doc))
        self.assertEqual(code, 0, out)
        meta = json.loads((doc / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual([p["title"] for p in meta["parts"]], [f"Section {n}" for n in range(1, 11)])

    def test_two_files_with_one_part_number_stop_the_build(self):
        doc = self.new_doc()
        self.write(doc / "src" / "03.html", part("New three"))
        self.write(doc / "src" / "3.html", part("Old three"))
        code, out = run("build", str(doc))
        self.assertEqual(code, 2, out)
        self.assertIn("part 3 has more than one file", out)

    def test_code_in_a_figure_or_the_reference_card_stops_the_build(self):
        for name, text in (("02.html", part("Two", '<figure><img src=x onerror="alert(1)"></figure>')),
                           ("reference.html", "<h2>Links</h2><script>alert(1)</script>")):
            with self.subTest(file=name):
                doc = self.new_doc()
                self.write(doc / "src" / "01.html", part("One"))
                self.write(doc / "src" / name, text)
                code, out = run("build", str(doc))
                self.assertEqual(code, 2, out)
                self.assertIn("could run code", out)
                self.assertFalse((doc / "part-1.html").exists())

    def test_refuses_a_folder_outside_the_library(self):
        site = self.tmp / "site"
        self.write(site / "src" / "404.html", part("Not found"))
        reference = self.write(site / "reference.html", "keep me")
        meta = self.write(site / "meta.json", '{"title": "Site"}')
        code, out = run("build", str(site))
        self.assertEqual(code, 1, out)
        self.assertIn("Refusing to build", out)
        self.assertEqual(reference.read_text(encoding="utf-8"), "keep me")
        self.assertEqual(meta.read_text(encoding="utf-8"), '{"title": "Site"}')
        self.assertFalse((site / "part-1.html").exists())

    def test_unreadable_meta_json_is_reported(self):
        doc = self.new_doc()
        self.write(doc / "src" / "01.html", part("One"))
        (doc / "meta.json").write_text("{not json", encoding="utf-8")
        code, out = run("build", str(doc))
        self.assertEqual(code, 1)
        self.assertIn("Can't read the title", out)

    def test_visuals_outside_a_figure_get_a_warning(self):
        doc = self.new_doc()
        self.write(doc / "src" / "01.html", part("One", "<p>Words.</p><table><tr><td>1</td></tr></table>"))
        code, out = run("build", str(doc))
        self.assertEqual(code, 0, out)
        self.assertIn("[outside a figure] <table>", out)

    def test_pages_carry_a_csp_and_a_pinned_mermaid(self):
        doc = self.new_doc()
        self.write(doc / "src" / "01.html",
                   part("One", '<p>Words.</p><figure><pre class="mermaid">graph TD; A--&gt;B</pre></figure>'))
        self.write(doc / "src" / "02.html", part("Two"))
        code, out = run("build", str(doc))
        self.assertEqual(code, 0, out)
        one = (doc / "part-1.html").read_text(encoding="utf-8")
        two = (doc / "part-2.html").read_text(encoding="utf-8")
        init_hash = base64.b64encode(hashlib.sha256(ra.MERMAID_INIT.encode()).digest()).decode()
        self.assertIn('http-equiv="Content-Security-Policy"', one)
        self.assertIn(f"'sha256-{init_hash}'", one)
        self.assertIn(f'src="{ra.MERMAID_SRC}" integrity="{ra.MERMAID_SRI}"', one)
        self.assertRegex(ra.MERMAID_SRC, r"mermaid@\d+\.\d+\.\d+/")
        self.assertIn("script-src 'none'", two)
        self.assertNotIn("<script", two)

    def test_library_page_has_no_inline_event_handlers(self):
        doc = self.new_doc()
        self.write(doc / "src" / "01.html", part("One"))
        run("build", str(doc))
        index = (self.lib / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("oninput", index)
        self.assertIn(ra.script_hash(ra.LIBRARY_FILTER), index)

    def test_opens_edge_only_when_asked(self):
        doc = self.new_doc()
        self.write(doc / "src" / "01.html", part("One"))
        run("build", str(doc))
        ra.open_in_edge.assert_not_called()
        code, out = run("build", str(doc), "--open")
        self.assertEqual(code, 0, out)
        ra.open_in_edge.assert_called_once()


class Init(LibraryTest):
    def test_sources_with_the_same_name_get_numbers(self):
        a = self.write(self.tmp / "a" / "README.md", "alpha")
        b = self.write(self.tmp / "b" / "README.md", "beta")
        x = self.write(self.tmp / "x" / "docs" / "plan.md", "x plan")
        y = self.write(self.tmp / "y" / "docs" / "plan.md", "y plan")
        doc = self.new_doc(a, b, x.parent, y.parent)
        source = doc / "source"
        self.assertEqual((source / "README.md").read_text(encoding="utf-8"), "alpha")
        self.assertEqual((source / "README-2.md").read_text(encoding="utf-8"), "beta")
        self.assertEqual((source / "docs" / "plan.md").read_text(encoding="utf-8"), "x plan")
        self.assertEqual((source / "docs-2" / "plan.md").read_text(encoding="utf-8"), "y plan")

    def test_folder_in_a_repo_copies_only_what_git_would_track(self):
        if not shutil.which("git"):
            self.skipTest("git isn't installed")
        repo = self.tmp / "repo"
        self.write(repo / ".gitignore", "dist/\n*.log\n")
        self.write(repo / "docs" / "design.md", "design")
        self.write(repo / "notes.md", "untracked but not ignored")
        self.write(repo / "dist" / "bundle.js", "ignored")
        self.write(repo / "app.log", "ignored")
        self.write(repo / ".env", "SECRET=fake")
        self.write(repo / ".venv" / "lib.py", "hidden")
        self.write(repo / "node_modules" / "m.js", "dependency")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        doc = self.new_doc(repo)
        self.assertEqual(self.copied(doc), ["repo/docs/design.md", "repo/notes.md"])

    def test_folder_outside_git_skips_hidden_and_dependency_folders(self):
        folder = self.tmp / "loose"
        self.write(folder / "a.md", "a")
        self.write(folder / "sub" / "b.md", "b")
        self.write(folder / ".env", "SECRET=fake")
        self.write(folder / "node_modules" / "m.js", "dependency")
        doc = self.new_doc(folder)
        self.assertEqual(self.copied(doc), ["loose/a.md", "loose/sub/b.md"])

    def test_refuses_sources_bigger_than_a_document(self):
        folder = self.tmp / "big"
        for n in range(5):
            self.write(folder / f"{n}.md", "words")
        with mock.patch.object(ra, "SOURCE_FILE_LIMIT", 3):
            code, out = run("init", "--project", "test", "--title", "Big", "--source", str(folder))
        self.assertEqual(code, 1)
        self.assertIn("Nothing was created", out)
        self.assertFalse((self.lib / "test").exists())

    def test_needs_a_project_when_there_is_no_source_file(self):
        code, out = run("init", "--title", "Pasted notes")  # the working folder is this repo, which mustn't count
        self.assertEqual(code, 3)
        self.assertIn("--project", out)

    def test_asks_for_the_text_of_files_the_fidelity_check_cant_read(self):
        pdf = self.write(self.tmp / "report.pdf", "%PDF-1.4")
        code, out = run("init", "--project", "test", "--title", "Report", "--source", str(pdf))
        self.assertEqual(code, 0, out)
        self.assertIn("source/report.md", out)


class Sources(LibraryTest):
    def test_every_page_lists_its_sources_hidden_from_read_aloud(self):
        a = self.write(self.tmp / "a" / "README.md", "alpha")
        b = self.write(self.tmp / "b" / "README.md", "beta")
        doc = self.new_doc(a, b)
        self.write(doc / "src" / "01.html", part("One", '<p>Words.<sup data-source="1,2"></sup></p>'))
        self.write(doc / "src" / "reference.html", "<h2>Links</h2><p>More.</p>")
        code, out = run("build", str(doc))
        self.assertEqual(code, 0, out)
        for page in ("part-1.html", "reference.html"):
            with self.subTest(page=page):
                text = (doc / page).read_text(encoding="utf-8")
                self.assertIn('<section class="sources" id="sources" aria-hidden="true">', text)
                self.assertIn(f'href="{a.resolve().as_uri()}"', text)
                self.assertIn('href="source/README-2.md"', text)  # the renamed copy, not b's own name

    def test_markers_become_hidden_links_and_bad_numbers_are_flagged(self):
        doc = self.new_doc(self.write(self.tmp / "a.md", "a"), self.write(self.tmp / "b.md", "b"))
        self.write(doc / "src" / "01.html", part(
            "One", '<h2>Setup<sup data-source="1, 2"></sup></h2><p>Words.<sup data-source="5"></sup></p>'))
        code, out = run("build", str(doc))
        self.assertEqual(code, 0, out)
        self.assertIn("[unknown source] A source marker names 5", out)
        self.assertNotIn("[no source markers]", out)
        text = (doc / "part-1.html").read_text(encoding="utf-8")
        self.assertIn('<sup class="src-ref" aria-hidden="true">[<a href="#source-1">1</a>, '
                      '<a href="#source-2">2</a>]</sup>', text)
        self.assertNotIn("data-source", text)
        self.assertEqual(json.loads((doc / "meta.json").read_text(encoding="utf-8"))["words"], 3)

    def test_several_sources_without_markers_get_a_nudge(self):
        doc = self.new_doc(self.write(self.tmp / "a.md", "a"), self.write(self.tmp / "b.md", "b"))
        self.write(doc / "src" / "01.html", part("One"))
        self.assertIn("[no source markers]", run("build", str(doc))[1])
        single = self.new_doc(self.write(self.tmp / "c.md", "c"))
        self.write(single / "src" / "01.html", part("One"))
        self.assertNotIn("[no source markers]", run("build", str(single))[1])

    def test_source_url_must_be_a_web_address(self):
        code, out = run("init", "--project", "test", "--title", "Page", "--source-url", "javascript:alert(1)")
        self.assertEqual(code, 1)
        self.assertIn("Nothing was created", out)
        self.assertFalse((self.lib / "test").exists())

    def test_web_page_is_listed_with_its_download(self):
        url = "https://example.com/docs/guide"
        code, out = run("init", "--project", "test", "--title", "Guide", "--source-url", url)
        self.assertEqual(code, 0, out)
        doc = Path(out.splitlines()[0])
        saved = re.search(r'curl\.exe -sL -o "([^"]+)"', out).group(1)
        self.write(Path(saved), "<p>Guide text.</p>")
        self.write(doc / "src" / "01.html", part("One"))
        self.assertEqual(run("build", str(doc))[0], 0)
        text = (doc / "part-1.html").read_text(encoding="utf-8")
        self.assertIn(f'<a href="{url}">', text)
        self.assertIn(f'href="source/{Path(saved).name}">saved copy</a>', text)

    def test_documents_from_before_sources_were_listed_still_get_a_list(self):
        source = self.write(self.tmp / "plan.md", "plan")
        doc = self.new_doc(source)
        meta = json.loads((doc / "meta.json").read_text(encoding="utf-8"))
        meta["sources"] = [str(source.resolve())]  # the old format: bare paths
        (doc / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        self.write(doc / "source" / "plan.md", "plan")
        self.write(doc / "src" / "01.html", part("One"))
        self.assertEqual(run("build", str(doc))[0], 0)
        self.assertIn('href="source/plan.md">saved copy</a>', (doc / "part-1.html").read_text(encoding="utf-8"))


class Fidelity(unittest.TestCase):
    def test_untagged_command_blocks_are_not_prose_but_ascii_diagrams_are(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "doc.md").write_text("Two words.\n\n```\nnpm run build --watch\n```\n\n"
                                           "```\n[client] --> [server]\n```\n", encoding="utf-8")
            (source / "LICENSE").write_text("license words that shouldn't count", encoding="utf-8")
            self.assertEqual(ra.source_prose_words(source), (4, 2))


class CommandLine(LibraryTest):
    def test_archived_and_all_cant_be_combined(self):
        code, out = run("list", "--archived", "--all")
        self.assertEqual(code, 2)
        self.assertIn("not allowed with", out)

    def test_delete_removes_the_document_and_its_index_entry(self):
        doc = self.new_doc()
        self.write(doc / "src" / "01.html", part("One"))
        self.assertEqual(run("build", str(doc))[0], 0)
        doc_id = doc.relative_to(self.lib).as_posix()
        code, out = run("delete", doc_id)
        self.assertEqual(code, 0, out)
        self.assertFalse(doc.exists())
        self.assertIn("unless Windows", out)
        self.assertNotIn(doc_id, (self.lib / "index.html").read_text(encoding="utf-8"))


@unittest.skipUnless(sys.platform == "win32", "the Edge lookup being tested is Windows-only")
class OpenInEdge(unittest.TestCase):
    def test_never_runs_an_msedge_planted_in_the_working_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            installed = tmp / "pf" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            installed.parent.mkdir(parents=True)
            installed.write_bytes(b"")
            repo = tmp / "repo"
            (repo / "Microsoft" / "Edge" / "Application").mkdir(parents=True)
            (repo / "Microsoft" / "Edge" / "Application" / "msedge.exe").write_bytes(b"")
            (repo / "msedge.cmd").write_text("@echo planted", encoding="utf-8")
            env = {"ProgramFiles(x86)": "", "ProgramFiles": str(tmp / "pf"), "LOCALAPPDATA": ""}
            cwd = os.getcwd()
            os.chdir(repo)
            try:
                with mock.patch.dict(os.environ, env), mock.patch.object(ra.subprocess, "Popen") as popen, \
                        redirect_stdout(io.StringIO()):
                    os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)  # Claude Code sets this; terminals don't
                    REAL_OPEN_IN_EDGE(tmp / "page.html")
            finally:
                os.chdir(cwd)
            popen.assert_called_once()
            self.assertEqual(Path(popen.call_args[0][0][0]), installed)


if __name__ == "__main__":
    unittest.main()
