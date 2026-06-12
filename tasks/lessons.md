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
