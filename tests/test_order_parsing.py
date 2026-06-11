"""Pure parsing tests for backend.browser.orders — no browser involved."""
from backend.browser.orders import extract_order_uuid, parse_card_text

# Verbatim live card innerText (2026-06).
CANCELLED_CARD = (
    "Dairy Queen\n"
    "$112.34 • 5 items • Personal\n"
    "Chicken Strip Baskets\n"
    "Order Cancelled"
)


def test_cancelled_card_verbatim() -> None:
    assert parse_card_text(CANCELLED_CARD) == {
        "store_name": "Dairy Queen",
        "description": "Chicken Strip Baskets",
        "items_count": 5,
        "price": 112.34,
        "cancelled": True,
        "dasher_name": "",
    }


def test_non_cancelled_card() -> None:
    text = ("McDonald's\n"
            "$23.45 • 2 items • Personal\n"
            "Big Mac Meal")
    parsed = parse_card_text(text)
    assert parsed["store_name"] == "McDonald's"
    assert parsed["description"] == "Big Mac Meal"
    assert parsed["items_count"] == 2
    assert parsed["price"] == 23.45
    assert parsed["cancelled"] is False


def test_missing_items_variant() -> None:
    text = ("Chipotle\n"
            "$15.00 • Personal\n"
            "Burrito Bowl")
    parsed = parse_card_text(text)
    assert parsed["items_count"] is None
    assert parsed["price"] == 15.00
    assert parsed["store_name"] == "Chipotle"
    assert parsed["description"] == "Burrito Bowl"
    assert parsed["cancelled"] is False


def test_single_item_singular() -> None:
    parsed = parse_card_text("Subway\n$9.99 • 1 item • Personal\nFootlong")
    assert parsed["items_count"] == 1
    assert parsed["price"] == 9.99


def test_cancelled_badge_is_not_description() -> None:
    parsed = parse_card_text("Wendy's\n$10.00 • 1 item\nOrder Cancelled")
    assert parsed["description"] == ""
    assert parsed["cancelled"] is True


def test_empty_text() -> None:
    assert parse_card_text("") == {
        "store_name": "",
        "description": "",
        "items_count": None,
        "price": None,
        "cancelled": False,
        "dasher_name": "",
    }


def test_uuid_extraction() -> None:
    href = "https://www.doordash.com/orders/c00336c7-5745-4891-baa3-7db6b02f3cdc"
    assert extract_order_uuid(href) == "c00336c7-5745-4891-baa3-7db6b02f3cdc"


def test_uuid_extraction_no_match() -> None:
    assert extract_order_uuid("https://www.doordash.com/orders/") is None
    assert extract_order_uuid("") is None
