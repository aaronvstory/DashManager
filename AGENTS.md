# AGENTS.md — working on DashManager

Read `CLAUDE.md` first; it is the canonical instruction file. Quick facts for
any coding agent:

- **Run:** `.venv\Scripts\python.exe -m backend` (NEVER the uvicorn CLI — the
  entrypoint sets the Windows Proactor loop policy Playwright needs).
  Frontend dev: `cd frontend && npm run dev`. Tests:
  `.venv\Scripts\python.exe -m pytest -q` (no browser required).
- **Never commit data:** `data/` (DB, sessions, cookies, screenshots) is
  gitignored; keep real customer details out of code/tests/docs.
- **Selectors only in `backend/browser/selectors.py`** or the settings table.
  Text/testid/label/href based — never hashed CSS classes. After any live
  re-verification, update the table in `HARVEST_NOTES.md`.
- **Correctness cores:** `refund_detector.py` (receipt `Refund −$X` vs
  `Total`; cancellation text proves nothing) and the chat rails (success =
  refund to ORIGINAL card; credits ≠ success; failures → `manual_flag` with
  transcript + screenshot, never silent).
- **Old app** `F:\claude\claudedasher\claudedasher\` is a read-only quarry;
  never open its `customers.json` / `*cookies*` / `*storage*`.
- **Style:** Python 3.13 typed, LF endings (`.gitattributes` pins this),
  comments only for non-obvious constraints. Frontend: TS strict, shadcn
  (base-nova/@base-ui), dark-default DoorDash theme.
