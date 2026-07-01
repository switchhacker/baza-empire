# Baza Bin — Design Spec

**Date:** 2026-06-30
**Author:** Serge Tkach (via Claude Code)
**Status:** Approved for planning

## Problem

The "Baza Terminal" Telegram bot (`baza-terminal-bot.service` → `agents/terminal/terminal_bot.py`)
is currently a **remote bash shell over Telegram** (send a command, it runs it in a persistent PTY and
streams output back). Serge wants to repurpose that bot into a **simple universal file-drop bridge**:
send *any* file of *any* type to the bot and it lands in a shared **Bin** that shows up in **Data Hub**
and can be selected from — as a file source — across both the Baza dashboard and the AHB123 surfaces.

## Decisions (locked during brainstorming)

1. **Full replace.** The Terminal bot becomes a pure file bin. **No command/shell execution over
   Telegram remains** (this also removes the remote-shell risk).
2. **Pick = copy.** Selecting a file from the bin into a target copies/references it and **leaves the
   original in the bin**. The bin is a durable library. Items are cleared manually.
3. **Flat, newest-first** layout in both Data Hub and the picker (search + type filter; no folders).
4. **Four pick-from-bin targets** wired now: AHB123 project files, the image/Sam picker, email
   attachments, and social posts.
5. **Storage off the git tree.** Bin files live at `/mnt/empirepool/bin/` on the ZFS pool — permanent,
   ~43 TB headroom, and *not* inside the auto-git'd `agent-framework-v3` tree (dropped media is never
   committed) and *not* under `cloud/1/` (kept separate from the Cloud tab).
6. **Shell code is decommissioned, not deleted.** `terminal_bot.py` moves to a `_deprecated/` folder;
   its systemd unit is disabled.

## Architecture

### Storage

- **Files:** `/mnt/empirepool/bin/` (flat; filenames prefixed with a timestamp to avoid collisions).
- **Index:** a dedicated **`bin.db`** SQLite database (isolated from `baza_projects.db` and `vision.db`),
  WAL mode + busy timeout consistent with the rest of the dashboard.
  - Table `bin_files`: `id, filename, stored_path, size, mime_type, kind, caption, source, tg_user_id, created_at`
  - `kind` ∈ {`image`, `document`, `video`, `audio`, `other`} (classified from MIME/extension).
  - `source` ∈ {`telegram`, `upload`}.

### Components (each with one clear job)

1. **`bin_store.py`** (new dashboard module) — the ONLY code that touches the bin. Public interface:
   - `add_file(data_or_path, *, filename, mime_type, caption, source, tg_user_id) -> item`
   - `list_items(*, q=None, kind=None, limit, offset) -> [item]` (newest-first)
   - `get(item_id) -> item`
   - `serve_token(item_id) -> token` / `resolve_token(token) -> abs_path` (base64-urlsafe of the
     relative path; **path-traversal guarded** — resolved path must stay under `/mnt/empirepool/bin/`;
     mirrors the existing `_pick_encode_token`/`_pick_decode_token` pattern)
   - `copy_to(item_id, dest_path) -> new_path` (the "pick = copy" primitive; original untouched)
   - `delete(item_id)` (removes row + file)
   - Owns the `bin.db` schema (create-if-missing on import) and the `/mnt/empirepool/bin/` directory.
   - Imported by BOTH the bot process and the dashboard → single source of truth.

2. **`bin_bot.py`** (replaces `terminal_bot.py`) — Telegram bot:
   - Same credentials: token env `TELEGRAM_TERMINAL`, allowlist env `TERMINAL_ALLOWED_USERS`
     (allowlist strictly enforced; non-allowed users rejected).
   - On a message carrying a file (`photo` / `document` / `video` / `audio` / `voice`): download via
     `get_file()` + `download_to_drive()`, sanitize the name, `bin_store.add_file(...)`, reply
     `✅ In the bin (N items)`.
   - On text: short help + `/count` and `/list` (last few items). **No command execution.**
   - Reuses the download/sanitize approach from `core/base_agent.py::handle_attachment`.
   - New unit **`baza-bin-bot.service`** (ExecStart → `agents/bin/bin_bot.py`); old
     `baza-terminal-bot.service` disabled.

3. **Data Hub "Bin" section** (`dashboard/templates/datahub.html` + routes) — flat newest-first grid
   (image thumbnails / type icons, name, size, caption, date). Per-item actions: download, delete,
   **Copy to…**, share. Includes a **drag-drop upload box** so files can be added from the browser too
   (no 20 MB limit on this path).
   - Routes: `GET /api/bin/list`, `GET /api/bin/serve/<token>`, `POST /api/bin/upload`,
     `POST /api/bin/delete`.

4. **Bin Picker modal** — one reusable **body-level** modal (per the "modals must be body-level" rule)
   embedded in both dashboards: search + type filter, multi-select, returns selected item ids.

### The four pick targets (each thin — routes through `bin_store.copy_to`)

| Target | Wiring | UI |
|--------|--------|-----|
| AHB123 project files | extend `/api/ahb/files` to accept `bin_item_id` → copy into project artifacts → `ahb_files` row | "From Bin" button in `project_detail.html` |
| Image / Sam picker | bin image mints a normal pick-token so it appears in the existing image picker | Bin source in the image picker modal |
| Email attachments | new `POST /api/email2/attachments/from-bin` copies into the outbox staging dir | "From Bin" button in email compose |
| Social posts | copy a bin image/video into the social asset pipeline | "From Bin" in the Social tab asset chooser |

## Data flow

```
Telegram file ─▶ bin_bot ─▶ bin_store.add_file ─▶ /mnt/empirepool/bin/ + bin.db
                                                         │
Browser drag-drop ─▶ /api/bin/upload ────────────────────┘
                                                         ▼
                              Data Hub "Bin" section  +  Bin Picker modal
                                                         │  (pick = copy)
                        ┌────────────────┬───────────────┼────────────────┐
                        ▼                ▼               ▼                ▼
                 ahb_files row     outbox staging   social asset    image pick-token
                 (project files)   (email attach)   (social post)   (Sam / image uses)
        original always remains in the bin
```

## Error handling & constraints

- **Telegram Bot API 20 MB download cap.** Files over ~20 MB are rejected by Telegram itself; the bot
  replies with a clear message ("too big for Telegram's 20 MB limit — drop it via the Data Hub upload
  box instead"). Lifting this needs a self-hosted Telegram Bot API server — out of scope. The browser
  drag-drop upload path has no such limit.
- **Allowlist** enforced in the bot; unauthorized Telegram users are ignored/refused.
- **Path-traversal**: all serve/copy operations resolve and assert the path stays under
  `/mnt/empirepool/bin/`.
- **Filename collisions**: timestamp prefix; names sanitized.
- **DB**: WAL + busy timeout (existing dashboard convention).
- **Disk**: pool has ~43 TB free; no quota needed initially.

## Testing (TDD)

- `bin_store` unit tests: add→list→get→serve round-trip; path-traversal rejection; `copy_to` leaves the
  original in place; `kind` classification from MIME/extension.
- Route tests: `/api/bin/list` shape; serve returns 200 for a valid token and 404 for a bad/forged one;
  each pick target's from-bin copy creates the correct destination row/file and does not remove the bin
  item; `/api/bin/upload` and `/api/bin/delete`.
- Bot: the file-handling function tested with a mocked Telegram update (mock `get_file` /
  `download_to_drive`); allowlist rejection path; text/`/count` path.

## Decommissioning

- Move `agents/terminal/terminal_bot.py` → `agents/terminal/_deprecated/terminal_bot.py`.
- Disable `baza-terminal-bot.service`; enable `baza-bin-bot.service`.
- Preserve the `TELEGRAM_TERMINAL` token and `TERMINAL_ALLOWED_USERS` allowlist (reused by the bin bot).

## Out of scope (YAGNI)

- Self-hosted Telegram Bot API server for >20 MB uploads.
- Folders / tags / auto-expiry in the bin (flat + manual clear is enough for v1).
- Bin surfaced in the Cloud tab (kept deliberately separate).
