---
name: read-aloud-clean
description: Cleans up the Read Aloud library at ~/ReadAloud, the listening pages made by the read-aloud skill. Use when the user wants to clean up, tidy, archive, hide, remove, restore, or delete read-aloud documents or notes, or clear old ones out of the library index. Examples include "clean up my read aloud notes", "take the billing portal doc out of the read aloud library", "archive read aloud pages older than a month", and "bring back the read aloud version of the session token design". Only for the Read Aloud library. When a request could mean tasks, notes, files, or email instead, ask. By default a document is only removed from the index and its files are kept. Files go to the Recycle Bin only when the user explicitly asks for a full delete.
---

# Read Aloud cleanup

The read-aloud skill files every document under `~/ReadAloud/<project>/<date>_<slug>/` and lists it on the library page, `~/ReadAloud/index.html`. The user keeps that page bookmarked and comes back to it often. Most documents are short-lived, so the library needs pruning, and the page itself can't delete anything. Cleanup happens here.

Make every change through the read-aloud skill's script, and never delete or move library files by hand. The script keeps the library page in sync, refuses anything outside the library, and never deletes anything outright on its own.

```bash
python "${CLAUDE_SKILL_DIR}/../read-aloud/scripts/read_aloud.py" <command> ...
```

The steps below shorten that to `read_aloud.py`.

## Two levels of removal

- **Archive is the default.** The document disappears from the library page, but its files stay where they are, and `restore` brings it back. The library page shows how many documents are archived.
- **Delete** moves the document's folder to the Recycle Bin and drops it from the page. Use it only when the user explicitly asks for the files to go, as in "delete them completely", "remove the files", or "free up the space". A plain "delete X" or "remove X" means archive, because the library page is what the user sees. When you archive in that case, say in your reply that the files were kept and can be deleted on request. If Windows can't recycle a folder, it asks the user before deleting it permanently.

Confirm the list with the user before a delete, even when they named the documents. Archiving documents the user named needs no confirmation, because it's fully reversible. A bulk archive picked by age or project does need it: show the matching titles first, because a rule like "older than a month" can catch more than the user expects.

Emptying the Recycle Bin is the user's call. Never do it, and never delete files permanently by any other means.

## Workflow

### 1. See what's there

```bash
python read_aloud.py list                    # documents on the library page
python read_aloud.py list --older-than 30    # created more than 30 days ago
python read_aloud.py list --project NAME     # one project
python read_aloud.py list --archived         # archived ones, for a restore or a full delete
```

Each document prints as an id, such as `billing-portal/2026-09-30_session-token-refresh-redesign`, followed by its title, date, number of parts, listening time, and size on disk.

### 2. Settle which documents

Make sure the request is about the Read Aloud library. Phrases like "clean up my notes" or "remove everything older than a month" can also mean tasks, notes, files, or email, so ask when the request doesn't say.

If the user named documents, a project, or an age, match those against the list. If they named nothing, ask.

When you ask, show a short list with each document's title, project, and age, never the raw ids. Also offer the bulk choices that fit, such as everything older than some number of days, or a whole project. If there are only a few documents, offer them as choices to pick from. Keep the list easy to scan, because a long one is hard for the user to take in.

### 3. Act

```bash
python read_aloud.py archive ID [ID ...]
python read_aloud.py restore ID [ID ...]
python read_aloud.py delete ID [ID ...]
```

Each command updates the library page. An id that matches nothing stops the command before anything changes.

### 4. Report back

Keep the reply to a line or two: what was archived, restored, or deleted, and how to undo it.
- **Undo an archive** with `restore`.
- **Undo a delete** by restoring the folder from the Recycle Bin, then running `python read_aloud.py library` to put it back on the page. If the user approved a permanent delete when Windows asked, it can't be undone.
