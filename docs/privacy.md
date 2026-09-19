# Privacy and data handling

- Local by default. Nothing leaves the machine unless `[upload] enabled = true`.
- Secrets are redacted at ingest, before hashing, so the hash chain covers the redacted form and the raw value is never stored.
- Tool outputs over the size limit are truncated in the ledger; a hash of the full content is kept.
- Donated study sessions: redacted on receipt; raw files deleted after redaction; only derived metrics are committed.
- If a hosted service ships: encryption at rest and in transit, 30-day default retention, delete on request, no training on user data, per-org isolation. This page is updated before that feature exists.
