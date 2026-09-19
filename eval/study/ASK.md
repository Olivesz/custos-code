# Recruitment: the ask (under 60 words) and the follow-up

## The ask
> We're building a tool that checks what an AI coding agent *says* it did against what it *actually* did (its own tool log). Runs locally, secrets redacted at ingest. Could you give us 20 minutes for a timed task, and optionally 5 recent Claude Code / Codex session logs? You get your own receipts and early access.

## Where to send it (in yield order)
1. HackMIT attendees and MIT peers who use a coding agent daily.
2. Authors of the public write-ups: the 249-session audit, red-handed, TruthGuard, Passproof, i-dont-believe-you, the piped-runner post. Ask for 20 minutes and a quote.
3. r/ClaudeAI, r/cursor, r/ChatGPTCoding (post by hand).
4. Claude Code / Cursor / Codex Discords (ask in the help channel, never scrape).
5. Show HN after the event.

## Follow-up (when they say yes)
- Send `CONSENT.md`; a reply of "I consent" is enough.
- Book 20 minutes; send the session-donation command the day before:
  `ls -t ~/.claude/projects/*/*.jsonl | head -5` (Claude Code) or `ls -t ~/.codex/sessions/*/*/*/*.jsonl | head -5` (Codex), zipped and sent over a private channel; we redact on receipt and delete the raw files.
- Add a row to `participants.csv` with an id only.

## Tracking
`eval/study/participants.csv`: `id, channel, agent, scheduled, consented, task_done, install_done, day7_check`. Ids only; no names in the repo.
