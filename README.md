# read-aloud

A personal Claude Code skill, `read-aloud`, that rewrites technical documents as HTML pages written for listening with Microsoft Edge's Read Aloud. It translates for the ear rather than summarizing. Lists, tables, and code become narrated prose, and exact code and commands move to a separate reference card.

## Install

Clone the repo, then link `skills/read-aloud/` into the global Claude Code skills folder. On Windows, from the clone's root:

```powershell
New-Item -ItemType Junction -Path "$HOME\.claude\skills\read-aloud" -Target "$PWD\skills\read-aloud"
```

On macOS or Linux:

```bash
ln -s "$PWD/skills/read-aloud" ~/.claude/skills/read-aloud
```

## Use

Ask Claude something like "make this doc something I can listen to", or run `/read-aloud`. Then press Ctrl+Shift+U in Edge.

Generated pages go to `~/ReadAloud/<project>/<date>_<slug>/`, outside every repo, including this one. Set `READ_ALOUD_HOME` to move the library. `~/ReadAloud/index.html` lists everything that has been generated.

## Layout

- `skills/read-aloud/SKILL.md` covers the workflow and the writing-for-the-ear rules.
- `skills/read-aloud/references/examples.md` has before and after conversions.
- `skills/read-aloud/scripts/read_aloud.py` handles `init`, `build`, and `library`: page template, speakability checks, the fidelity check, the library index, and opening Edge. It needs only the Python standard library.
