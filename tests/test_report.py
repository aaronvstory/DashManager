"""Tests for the daily HTML report renderer (pure, no browser, no DB)."""
from backend import report


def _sample_model() -> dict:
    return {
        "date": "2026-06-12",
        "generated_at": "2026-06-12 09:30 UTC",
        "customers": [
            {
                "id": 1, "first_name": "Ada", "last_name": "Vance",
                "phone": "+1 863 555 0142", "email": "av@example.net",
                "session_status": "active", "password": "S3cr3tPass!",
                "number_token": "tok1234567890abcdef", "_seq": 1,
                "_copy_id": "06-12 1 Ada", "created_at": "2026-06-10 12:00:00",
                "screenshots": [{"kind": "orders", "label": "Orders page",
                                 "path": "x/data/screenshots/2026-06-12/c1_orders.png"}],
                "notes": "created via signup · 901 Bayshore Blvd, Tampa, FL · daisy:abc",
                "orders": [
                    {"id": 10, "store_name": "Dairy Queen",
                     "description": "2 items", "price": 112.24,
                     "refund_status": "refunded", "refund_amount": 112.24,
                     "total_amount": 112.24, "claims": [], "chats": []},
                    {"id": 11, "store_name": "Chipotle", "description": "1 item",
                     "price": 107.01, "refund_status": "pending_claim",
                     "refund_amount": None, "total_amount": None,
                     "claims": [{"amount": 107.01, "to_original_payment": 1,
                                 "confirmed": 1, "outcome": "confirmed",
                                 "error": None}],
                     "chats": []},
                ],
            },
            {
                "id": 2, "first_name": "Bo", "last_name": "Kim",
                "phone": "", "email": "", "session_status": "active",
                "notes": "no-orders state verified", "orders": [],
            },
            {
                "id": 3, "first_name": "Cleo", "last_name": "Ng",
                "phone": "+1 555 0007", "email": "c@example.net",
                "session_status": "expired", "notes": "imported",
                "orders": [
                    {"id": 30, "store_name": "Wendy's", "description": "",
                     "price": 88.0, "refund_status": "not_refunded",
                     "refund_amount": None, "total_amount": None, "claims": [],
                     "chats": [{
                         "id": 300, "outcome": "success", "agent_reached": 1,
                         "attempt_no": 1,
                         "messages": [
                             {"direction": "out", "content": "AGENT"},
                             {"direction": "system",
                              "content": "connected to our support agent"},
                             {"direction": "out",
                              "content": "Please refund $88.00 to my original card."},
                             {"direction": "in",
                              "content": "I've issued the refund to your original "
                                         "payment method."},
                         ],
                     }],
                     },
                ],
            },
        ],
        "summary": {"customers": 3, "orders": 3, "refunded": 1, "pursuing": 2,
                    "no_orders": 1, "needs_you": 0},
    }


def test_render_is_self_contained_html():
    out = report.render_report(_sample_model())
    assert out.startswith("<!doctype html>")
    assert "<style>" in out and "</style>" in out
    # inline JS + LOCAL relative <img> are allowed, but NO external resources —
    # opens straight from disk, survives offline. (img src uses ../screenshots.)
    assert "http://" not in out and "https://" not in out
    assert "cdn" not in out.lower()
    assert 'src="http' not in out and 'src="//' not in out


def test_render_shows_customers_orders_and_transcript():
    out = report.render_report(_sample_model())
    assert "Ada Vance" in out
    assert "Dairy Queen" in out and "Chipotle" in out
    assert "$112.24" in out
    # address pulled out of the notes free-text
    assert "901 Bayshore Blvd, Tampa, FL" in out
    # transcript bubbles rendered
    assert "Please refund $88.00 to my original card." in out
    assert "issued the refund to your original payment method" in out
    # pretty date header
    assert "Friday, June 12, 2026" in out


def test_summary_cards_present():
    out = report.render_report(_sample_model())
    for label in ("Customers", "Orders", "Refunded", "Pursuing", "Needs you"):
        assert label in out


def test_html_escaping_blocks_injection():
    model = _sample_model()
    model["customers"][0]["first_name"] = "<script>alert(1)</script>"
    out = report.render_report(model)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_empty_day_renders_placeholder():
    out = report.render_report({
        "date": "2026-06-12", "generated_at": "x",
        "customers": [], "summary": {
            "customers": 0, "orders": 0, "refunded": 0, "pursuing": 0,
            "no_orders": 0, "needs_you": 0}})
    assert "Nothing on the board yet" in out


def test_needs_you_logic():
    # not_refunded with no winning chat -> needs you
    o = {"refund_status": "not_refunded", "chats": []}
    assert report._order_needs_you(o) is True
    # refunded -> never needs you
    assert report._order_needs_you({"refund_status": "refunded"}) is False
    # not_refunded but a chat won -> resolved
    o2 = {"refund_status": "not_refunded",
          "chats": [{"outcome": "success"}]}
    assert report._order_needs_you(o2) is False


def test_money_and_address_helpers():
    assert report._money(112.2) == "$112.20"
    assert report._money(None) == "—"
    assert report._money("bad") == "—"
    assert report._address({"notes": "created via signup · 12 Main St · daisy:x"}) \
        == "12 Main St"
    assert report._address({"notes": ""}) == "—"


def test_address_from_imported_notes():
    # adopted accounts: "imported from CustomerDaisy · <address>"
    n = {"notes": "imported from CustomerDaisy · 1231 Macedonia Rd, Edenton, NC"}
    assert report._address(n) == "1231 Macedonia Rd, Edenton, NC"


def test_account_details_capture_all_info():
    out = report.render_report(_sample_model())
    # operational fields surfaced
    assert "06-12 1 Ada" in out          # copy id
    assert "+1 863 555 0142" in out      # phone
    assert "av@example.net" in out       # email
    assert "901 Bayshore Blvd" in out    # address
    assert "Added" in out and "2026-06-10" in out  # date


def test_password_is_masked_not_plaintext_in_body():
    out = report.render_report(_sample_model())
    # the secret value lives in a data-val attr (for reveal), but the visible
    # default is masked dots, never shown inline as the label's text
    assert 'data-val="S3cr3tPass!"' in out
    assert "secret" in out and "reveal" in out


def test_copy_and_dropdown_affordances_present():
    out = report.render_report(_sample_model())
    assert "dmCopy" in out and "data-copy=" in out   # copy buttons
    assert "<details" in out                          # collapsible cards
    assert "Expand all" in out and "Collapse all" in out


def test_short_id_format():
    sid = report._short_id({"first_name": "Kelly"}, "2026-06-13", 1)
    assert sid == "06-13 1 Kelly"


def test_render_index_lists_days():
    model = {"generated_at": "2026-06-13 10:00 UTC", "entries": [
        {"bucket": "2026-06-13", "pretty": "Saturday, June 13, 2026",
         "file": "2026-06-13.html", "customers": 6, "active": 6},
        {"bucket": "2026-06-11", "pretty": "Thursday, June 11, 2026",
         "file": "2026-06-11.html", "customers": 5, "active": 4}]}
    out = report.render_index(model)
    assert out.startswith("<!doctype html>")
    assert "2026-06-13.html" in out and "2026-06-11.html" in out
    assert "6 customers" in out and "6 live" in out


def test_render_index_empty():
    out = report.render_index({"generated_at": "x", "entries": []})
    assert "No reports yet" in out


def test_proof_rel_makes_relative_path():
    p = r"F:\claude\DashManager\data\screenshots\2026-06-13\c1_orders.png"
    assert report._proof_rel(p) == "../screenshots/2026-06-13/c1_orders.png"
    # forward-slash variant
    p2 = "data/screenshots/2026-06-13/c1_receipt_5.png"
    assert report._proof_rel(p2) == "../screenshots/2026-06-13/c1_receipt_5.png"


def test_proof_row_renders_thumbnails():
    shots = [
        {"kind": "orders", "label": "Orders page",
         "path": r"x\data\screenshots\2026-06-13\c1_orders.png"},
        {"kind": "receipt", "label": "Dairy Queen receipt",
         "path": r"x\data\screenshots\2026-06-13\c1_receipt_5.png"},
    ]
    out = report._proof_row(shots)
    assert "Orders page" in out and "Dairy Queen receipt" in out
    assert "../screenshots/2026-06-13/c1_orders.png" in out
    assert "<img" in out and "loading=\"lazy\"" in out


def test_proof_row_empty():
    assert "no screenshots yet" in report._proof_row([])


# ── resolution method derivation ─────────────────────────────────────────────


def test_method_already_refunded():
    o = {"refund_status": "refunded", "claims": [], "chats": []}
    assert report.resolution_method(o)[0] == "Already refunded"


def test_method_self_claim():
    o = {"refund_status": "refunded",
         "claims": [{"confirmed": 1, "amount": 112.24,
                     "to_original_payment": 1}],
         "chats": []}
    label, conf = report.resolution_method(o)
    assert label == "Self-claim"
    assert "to original card" in conf


def test_method_credits_to_card_agent_chat():
    o = {"refund_status": "refunded", "claims": [],
         "chats": [{"outcome": "success", "agent_reached": 1, "messages": [
             {"direction": "out", "content": "convert credits please"},
             {"direction": "in", "content":
              "both amounts have been issued back to the charged card. "
              "I have exchanged the credits for a refund."}]}]}
    label, conf = report.resolution_method(o)
    assert label == "Credits→card (agent chat)"
    assert "exchanged the credits" in conf


def test_method_agent_chat_plain():
    o = {"refund_status": "refunded", "claims": [],
         "chats": [{"outcome": "success", "agent_reached": 1, "messages": [
             {"direction": "in", "content":
              "I've refunded $88 to your original payment method."}]}]}
    assert report.resolution_method(o)[0] == "Agent chat"


def test_method_pending():
    o = {"refund_status": "not_refunded", "claims": [], "chats": []}
    assert report.resolution_method(o)[0] == "Pending"


def test_breakdown_table_totals_and_methods():
    orders = [
        {"store_name": "Dairy Queen", "price": 112.44, "refund_status":
         "refunded", "refund_amount": 112.44, "last_checked_at":
         "2026-06-13 02:00:00", "claims": [], "chats": [
             {"outcome": "success", "agent_reached": 1, "messages": [
                 {"direction": "in", "content":
                  "issued back to the charged card, exchanged the credits"}]}]},
        {"store_name": "Dairy Queen", "price": 112.34, "refund_status":
         "refunded", "refund_amount": 112.34, "last_checked_at":
         "2026-06-13 02:00:00",
         "claims": [{"confirmed": 1, "amount": 112.34,
                     "to_original_payment": 1}], "chats": []},
    ]
    out = report._breakdown_table(orders)
    assert "<table" in out and "Confirmation" in out
    assert "$112.44" in out and "$112.34" in out
    assert "Credits→card (agent chat)" in out
    assert "Self-claim" in out
    assert "2/2" in out
    assert "$224.78 refunded to card" in out
