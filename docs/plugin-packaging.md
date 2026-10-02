# Packaging read-aloud as a plugin

Status: not done yet. This is the plan for later.

Today the skill is installed by cloning the repo and linking `skills/read-aloud/` into `~/.claude/skills/`. Claude Code has no command that installs a plain skill straight from GitHub. To install from the repo without cloning, the repo has to be packaged as a plugin, and the same repo can also serve as the marketplace that lists it.

Research date: 2026-10-02, from the Claude Code docs. Re-check the docs before doing this, since the plugin system changes often.

## What it would take

Add two files. The existing `skills/read-aloud/` folder stays where it is, because a plugin picks up skills from a `skills/` folder at its root.

```
read-aloud/
  .claude-plugin/
    plugin.json        the plugin manifest; the repo root is the plugin
    marketplace.json   the catalog that lists this one plugin
  skills/read-aloud/   unchanged
```

`.claude-plugin/plugin.json`:

```json
{
  "name": "read-aloud",
  "description": "Rewrites technical documents as HTML pages written for Edge Read Aloud",
  "author": { "name": "Ryan Howard" }
}
```

`.claude-plugin/marketplace.json`:

```json
{
  "name": "rwhoward",
  "owner": { "name": "Ryan Howard" },
  "plugins": [
    {
      "name": "read-aloud",
      "source": "./",
      "description": "Rewrites technical documents as HTML pages written for Edge Read Aloud"
    }
  ]
}
```

Using `"source": "./"` to mean "the repo root is the plugin" is my reading of the docs. Confirm it, or fall back to moving the plugin under `plugins/read-aloud/` and pointing `source` there.

## Installing it

From a shell:

```bash
claude plugin marketplace add rwhoward/read-aloud
claude plugin install read-aloud@rwhoward
```

The same commands work inside a session as `/plugin marketplace add ...` and `/plugin install ...`. Updates come from `claude plugin marketplace update rwhoward`, or from auto-update if it's turned on for the marketplace.

## Changes the skill would need

- **The script path.** SKILL.md currently calls `$HOME/.claude/skills/read-aloud/scripts/read_aloud.py`. A plugin install lives in Claude Code's plugin cache instead, so that path breaks. Switch SKILL.md to refer to the script relative to the skill's base directory, which Claude Code shows when the skill loads. That works for both kinds of install. The docs also mention a `${CLAUDE_PLUGIN_ROOT}` variable, but it only exists for plugins.
- **The skill names.** Plugin skills are namespaced as `<plugin>:<skill folder>`. Kept as they are, the skills would become `read-aloud:read-aloud` and `read-aloud:read-aloud-clean`. Rename the folders to `skills/create/` and `skills/clean/` to get `read-aloud:create` and `read-aloud:clean`, and update each SKILL.md's `name` to match. Check that both descriptions still trigger the same way.
- **The shared script.** Both skills use `skills/read-aloud/scripts/read_aloud.py`. In a plugin, move it to `scripts/read_aloud.py` at the plugin root, so both skills can reach it through `${CLAUDE_PLUGIN_ROOT}/scripts/read_aloud.py`.

## Why colon names need a plugin

As of 2026-10-02, the Claude Code docs give only two ways to get a `prefix:name` skill:

- **Plugins.** The prefix is the plugin's name.
- **Nested project skills.** In a repo, a skill in a subfolder's `.claude/skills`, such as `apps/web/.claude/skills/deploy`, shows up as `apps/web:deploy`. The prefix is the folder path, and this only works for project skills, not global ones.

A skill's `name` can't contain a colon. The discovery of global skills in `~/.claude/skills/` is also flat, so a nested folder like `~/.claude/skills/read-aloud/create/` isn't picked up. That's why the global install uses the separate names `read-aloud` and `read-aloud-clean`.

## Gotchas

- On the machine you develop on, keep the linked install. Edits take effect immediately, while a plugin install is a cached copy that only changes after you push and update.
- Don't install both kinds on one machine, or there will be two copies of the skill competing to trigger.

## Docs

- Installing plugins: https://code.claude.com/docs/en/plugins/install
- Creating a marketplace: https://code.claude.com/docs/en/plugins/create-marketplace
- Marketplace reference: https://code.claude.com/docs/en/plugins/marketplace-reference
- Plugin components and path variables: https://code.claude.com/docs/en/plugins/components
