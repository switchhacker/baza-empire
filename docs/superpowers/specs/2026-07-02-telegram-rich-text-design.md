# Telegram Rich Text — Design Spec

**Date:** 2026-07-02
**Status:** Approved by Serge (scope: everything outbound)

## Problem

Agent LLMs already produce markdown (bold, headers, bullet lists, code), but
`BaseAgent._send_response` (core/base_agent.py) runs `_strip_markdown()` and sends
plain text with no `parse_mode` — every agent reply arrives in Telegram as a flat
wall of text. Cron digests and notification scripts are inconsistent: some send
HTML, some plain, each with its own chunking (or none).

Telegram renders HTML natively (`<b> <i> <u> <s> <code> <pre> <a> <blockquote>`).
We should convert the markdown we already have instead of stripping it, and give
all outbound paths one consistent, pretty house style.

## Decision

Hand-rolled shared formatter — no new pip dependencies (local-first, 9 services).
MarkdownV2 rejected (brittle escaping → bounced messages). `telegramify-markdown`
dependency rejected (needless dep, less control).

## Components

### 1. `core/telegram_fmt.py` (new)

**`md_to_html(text: str) -> str`** — converts LLM-flavored markdown to
Telegram-safe HTML:

- Escape `& < >` FIRST (before inserting any tags).
- Fenced ``` blocks → `<pre>` (contents escaped, no inner conversion; language
  hint → `<pre><code class="language-x">`).
- Inline `` `code` `` → `<code>` (no inner conversion).
- `**bold**` / `__bold__` → `<b>`; `*italic*` / `_italic_` → `<i>` (word-boundary
  aware; underscores inside words/paths untouched).
- `# … ######` headers → `<b>Header</b>` on its own line.
- `- ` / `* ` bullets → `• ` (nested indent preserved); numbered lists kept as-is.
- `- [x]` / `- [ ]` checklist items → `✅` / `☐`.
- `[text](url)` → `<a href="url">text</a>` (http/https only; other schemes → text).
- Markdown tables → `<pre>` block (monospace alignment).
- `---` horizontal rule → `───────` line.
- Idempotence guard: text that already looks like Telegram HTML (contains
  `<b>`/`<pre>` etc. and no markdown markers) passes through with only a
  well-formedness check.

**`send_html(bot, chat_id, text, **kwargs)`** (async) and
**`post_html(token, chat_id, text)`** (sync `requests`, for cron scripts):

- Convert via `md_to_html`, chunk at ≤4000 chars on line boundaries, never
  splitting inside a tag or `<pre>` block (close + reopen `<pre>` across chunks).
- Send with `parse_mode="HTML"`.
- On Telegram 400 "can't parse entities": resend THAT chunk as stripped plain
  text (`strip_markdown` moves here from base_agent). A message is never lost
  to a formatting bug. Log a warning with the offending snippet.
- 0.3s inter-chunk delay (existing behavior).

**`strip_markdown(text) -> str`** — relocated from base_agent; the fallback path
and available for anything that truly wants plain text.

### 2. Wire-in points

| Path | Change |
|------|--------|
| `core/base_agent.py _send_response` | Replace strip+plain-send with `send_html`. Claim-verifier / auto-DISPATCH flow untouched (runs before formatting). |
| `core/task_runner.py` notifications | `post_html` / `send_html` |
| `agents/cron_helpers.py` | shared sender |
| `scripts/duke_morning_digest.py` | shared sender |
| `agents/simon_bately/briefing_cron.py` | shared sender |
| `scripts/hallucination_weekly_digest.py` | shared sender |
| `skills/shared/infra_report.py`, `research_report.py`, `send_telegram.py`, `suggest_action.py` | route through `md_to_html` + fallback (send_telegram already uses HTML but has no escaping/fallback — fixes latent parse-error bug) |
| `core/commander.py`, `core/approval.py` (already HTML) | adopt shared chunker/fallback only; their hand-built HTML is passed through |

Specter's phantom bridge (`agents/specter_voss/openclaw/telegram_bridge.py`) is
out of scope this pass (separate host, rsync later — same module works there).

### 3. House style (make output organized, not just rendered)

One shared block appended in the base system-prompt builder (single place in
`base_agent.py` / scaffold prompt assembly — NOT 9 persona files):

- Bold one-line header when a reply has structure; plain short sentences for
  simple answers (no forced structure on chit-chat).
- Status marks: ✅ done / ⚠️ needs attention / ❌ failed / ☐ todo.
- `•` bullets for lists, checklists for multi-step work.
- `code` for paths, commands, service names.
- Keep Telegram replies compact; no giant headers, no nested markdown tables.

### 4. Testing & rollout

- TDD: `tests/test_telegram_fmt.py` — escaping (`& < >` and `<3`, `a<b` cases),
  bold/italic/code/pre, headers, bullets, checklists, links (incl. `javascript:`
  rejection), tables, idempotence, chunking (tag-safe boundary, `<pre>` reopen),
  fallback on simulated 400.
- Integration: base_agent send path test with a mocked bot asserting
  `parse_mode="HTML"` + fallback resend.
- Live smoke: one agent sends a formatted test message to Serge's chat.
- Restart all 8 `baza-agent-*` services (hyphenated names) + verify active.
- Auto-git commits within the hour; no manual commit.

## Risks

- Regex converter edge cases → mitigated by plain-text fallback per chunk.
- Double-escaping already-HTML senders (commander) → idempotence guard + they
  use the pass-through chunker entry point.
- LLMs over-formatting after house-style prompt → style block explicitly says
  plain prose for simple answers.
