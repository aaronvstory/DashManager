# DashManager — Lessons

## Agent confirmation must be VERBATIM + per-amount
- **Mistake:** Accepted Víctor's singular "the refund has been issued back to
  the original payment method" as proof for BOTH of Paula's orders ($112.44 +
  $112.34). One singular confirmation does NOT prove two orders were each
  converted.
- **Rule:** When a chat covers multiple orders, the agent MUST write out EACH
  amount verbatim, each explicitly confirmed exchanged/refunded onto the
  original card. Don't close until every amount is individually confirmed in
  the agent's own words.
- **Context:** Any support chat handling >1 order (credits→card conversion or
  refund pursuit). Re-push per-amount until each is named + confirmed.

## Don't over-sleep in live agent chats
- **Mistake:** Used a long (45s+) sleep waiting for the agent; the agent
  session/input dropped while idle.
- **Rule:** In a LIVE agent chat, poll in short intervals (~8-12s) and reply
  promptly. Long idle gaps risk the agent disconnecting or the input vanishing.
- **Context:** desktop-commander REPL driving a live DoorDash support chat.

## Smaller browser window so the user can see it
- **Rule:** Launch headed browsers at ~1200x720 (window-size + viewport), not
  1400x900 — when the user resizes to fit their screen, taller windows cut off
  at the bottom.

## Active-drive long-running jobs — never yield the turn to "wait"
- **Mistake:** Scheduled ScheduleWakeup and ENDED the turn to wait for a background browser job; 20 min passed with zero progress because nothing re-invoked me promptly and I wasn't watching.
- **Rule:** For a job I'm actively shepherding, BLOCK in-turn with the allowed Monitor pattern (`until <condition>; do sleep N; done` in a foreground Bash call) so I keep driving, OR run the job in-process and stream output. Only use ScheduleWakeup as a *fallback heartbeat*, never as the primary "I'll come back later" when the user is waiting on a result NOW.
- **Context:** Any supervised live test / deploy / CI watch where the user expects continuous progress. Polling cadence ≤30s for fast-moving state; print every state change.

## DoorDash signup — the OLD working recipe (from F:\iCloudDrive\F\dash\script\.wolf*.py)
- **Finding:** Old working signup = `undetected_chromedriver` + REAL Chrome binary (`options.binary_location = ".../chrome.exe"`), only 3 cosmetic args (disable-notifications/infobars/mute-audio). NO mobile emulation, NO custom UA, NO proxy — ran on the home residential IP. Navigated to `consumer/login/` then clicked "Sign Up" (not direct /signup). Window 1023x919.
- **Rule:** "Mobile mode" was a misremember — it was desktop UC Chrome on a clean home IP. The proxy may be HURTING (slow gateway / flagged residential). Test direct-IP UC Chrome as a first-class path.
- **Context:** DashManager account creation. Selectors then were ID-based (FieldWrapper-N) — likely changed; key on autocomplete attrs now.
