# 🚀 AUTONOMOUS HANDOFF — Automated Customer Creation

**You run this ENTIRELY ALONE. The user is gone and will not respond. Do NOT
wait for, ask, or depend on the user for anything.** Read this, then
`SIGNUP_CDP_SPIKE_PLAN.md` for deep technical detail. Work for as long as it
takes; report results at the end.

## OPERATING RULES (do not violate)
1. **No user dependency.** Never "ask", "confirm", "be ready", "decide together",
   or pause for approval. Every fork in the road: **pick the best option, write
   down why, proceed.** AskUserQuestion is BANNED this session.
2. **Auto-heal mantra.** Any failure (403, dead proxy, OTP lag, bad selector,
   timeout) → heal in-flight (retry, rotate proxy, re-login, re-fetch) →
   investigate the real cause yourself → fix at root (code+tests+docs) → keep
   going. A fixable condition is never a stopping point.
3. **Self-merge your own work.** Each piece = own branch off main → commit →
   push → open PR → wait for bots (poll ~4 min) → fix actionable findings →
   when green + your own code-reviewer subagent is satisfied, **squash-merge it
   yourself**, sync main, move on. The user pre-authorized merging for THIS
   autonomous session. (Do NOT merge anything that's red or has unaddressed
   high-confidence findings.)
4. **Money/stealth safety still applies.** Zero-tolerance refund gates untouched.
   Burning ~8¢ api.cc numbers to test signup is expected and fine.
5. **No secrets in git.** Proxy creds live in `working-proxies.txt` (gitignored)
   / `.env`. Never put them in code, API responses, or commits.

## GOAL
Get automated DoorDash account creation WORKING (we failed before; retrying with
modern SeleniumBase **CDP Mode** + **residential proxies** — two fixes for two
root causes). Build the supporting app features regardless of whether the bot
gate ultimately falls, so the user returns to maximum progress either way.

---

## BUILD ORDER (each is an independent PR you merge yourself)

### PR A — Proxy liveness + Proxy Manager app section
Independent of signup success, so build it first (guaranteed value, no bot gate):
- `backend/browser/proxy_pool.py`: read `working-proxies.txt`, normalize to
  `user:pass@host:port`, and a `check_proxy()` that routes a request THROUGH the
  proxy to an IP-echo (api.ipify.org / lumtest.com/myip.json) returning
  {alive, exit_ip, country, city, latency_ms}. Reuse it everywhere.
- `/api/proxies` route (list + per-proxy test). Creds never in the response.
- New **Proxy** page/tab in the web app (brutalist — reuse `.bx/.eyebrow/.num`,
  square, hard borders, mono). Shows each proxy: alive?, exit IP, location,
  latency; a "Test all" action. Verify visually via Playwright screenshots.
- Tests for the pure parts (parse line → proxy dict; format → SB proxy string).

### PR B — Signup via CDP Mode (THE experiment)
Follow `SIGNUP_CDP_SPIKE_PLAN.md` exactly:
- Verify `sb.cdp.solve_captcha` at runtime; `uv pip install -U seleniumbase` if absent.
- Smoke-test CDP stealth on gitlab sign_in FIRST (cheap sanity).
- Rewrite the signup driver: `SB(uc=True, test=True, locale="en", proxy=<residential>)`
  → `activate_cdp_mode(SIGNUP_URL)` → `sb.cdp.solve_captcha()` → fill 5 fields
  (`sb.cdp.press_keys`) → submit → (bot gate fires HERE) → heal/solve → OTP →
  export Playwright storage_state. Reuse selectors + OTP logic from `uc_signup.py`.
- Run it HEADED yourself (you don't need the user watching — capture screenshots
  at each stage as the audit trail). Try 2-3 numbers. Rotate proxy on failure.
  **Decide the verdict from evidence: does it pass `user_assessment_bot`
  repeatably?** Write the verdict + screenshots into a results file
  (`SIGNUP_RESULTS.md`) regardless of outcome.
- If it works: wire `signup_via_cdp` into `account_creator.create_account`.
- If it doesn't: exhaust the Mintz levers yourself (unbranded `use_chromium=True`,
  `uc_gui_click_captcha` PyAutoGUI fallback, different proxy region, CF-clearance
  cookie reuse) before concluding. Document exactly how far the gate let you get.

### PR C — Live OTP in the app
The user needs to grab a created account's OTP to log in on their phone later.
- Surface the existing api.cc poller (`backend.otp_fetch` / `DaisyBridge.fetch_otp`)
  in the web app: a live-refreshing OTP view (like CustomerDaisy's "Live SMS
  Codes") AND per-customer on-demand fetch. **Decision (made for you): build BOTH
  a batch live-table for the bucket AND a per-row fetch button** — that covers
  "X at a time" without asking. Codes expire ~30s → poll/refresh + show freshness.
- There's already an `otp_fetch` CLI + `FetchOtpDialog` — extend, don't reinvent.

### (Do NOT do without it being trivially safe) Port CustomerDaisy
Large. Skip unless A-C are done AND it's clearly low-risk. Note it in
SIGNUP_RESULTS.md as a future option; don't start it.

---

## VERIFIED FACTS (don't relearn)
- seleniumbase **4.49.10**: `activate_cdp_mode` ✅, `sb_cdp` ✅. `sb.cdp.solve_captcha`
  added 4.44.2 (likely present; upgrade if not).
- Pattern: `SB(uc=True, test=True, locale="en")` → `sb.activate_cdp_mode(url)` →
  `sb.cdp.solve_captcha()`. OLD `Driver(uc=True)+uc_open_with_reconnect` in
  `uc_signup.py` = the deprecated path that failed — replace it.
- Proxy: HTTP gateway `resident.lightningproxies.net:8080`, creds in
  `working-proxies.txt` (gitignored). `SB(proxy="user:pass@host:port")` =
  browser-scoped only. Rotation via username flags, not an API.
- Failed-signup numbers are REUSABLE. ~8¢ each.
- Bot gate fires on SUBMIT, not page load — the interactive unknown the test answers.
- Runner already auto-heals expired sessions (open_receipt raises on login-redirect
  → re-login + retry). Mirror that spirit in signup.

## REPO STATE (start of this session)
- main clean; merged: #17 refund_run CLI, #18 brutalist overhaul, #19 scraper
  fix, #20 plan doc, #21 session auto-heal, #22 this handoff. No open PRs.
- 249 tests pass. Backend: `.venv\Scripts\python.exe -m backend` (Proactor loop).
- Auto-heal mantra is in global `~/.claude/CLAUDE.md` + memory `feedback-auto-heal-mantra`.

## START
Just begin with PR A (proxy manager) — guaranteed-value and no external
dependency. Then PR B (the experiment), then PR C. Self-merge each. At the end,
write `SIGNUP_RESULTS.md` with: what works, the signup verdict + screenshots,
what you healed/learned, and what (if anything) genuinely needs the user (e.g.
"signup blocked even with X/Y/Z — here's the evidence"). That results file is the
only thing the user reads when back — make it complete.
