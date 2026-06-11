"""Pure order-state classification + dasher extraction (no browser)."""
from backend.browser.orders import (classify_orders_page, in_progress_status,
                                    parse_card_text)


class TestClassifyPage:
    def test_empty_account(self):
        assert classify_orders_page(
            "Orders\nNo Previous Deliveries, Order Today!") == "none"

    def test_in_progress(self):
        txt = ("Orders\nIn Progress\nDoubleDash\nOrder in progress\n"
               "Arrives between: 3:55 - 3:58 AM\nDairy Queen\nHeading to you")
        assert classify_orders_page(txt) == "in_progress"

    def test_preparing_is_in_progress(self):
        assert classify_orders_page("Dairy Queen\nPreparing") == "in_progress"

    def test_dasher_waiting_is_in_progress(self):
        assert classify_orders_page(
            "Dairy Queen\nDasher waiting for order\n"
            "Estimated Delivery: Jun 12") == "in_progress"

    def test_picking_up_is_in_progress(self):
        assert classify_orders_page(
            "Dairy Queen\nPicking up your DoubleDash order") == "in_progress"

    def test_completed_only(self):
        assert classify_orders_page(
            "Orders\nCompleted\nDairy Queen\nView Receipt") == "has_completed"


class TestInProgressStatus:
    def test_heading_to_you(self):
        assert in_progress_status("Dairy Queen\nHeading to you").lower() \
            == "heading to you"

    def test_preparing(self):
        assert "preparing" in in_progress_status(
            "Dairy Queen\nPreparing").lower()

    def test_completed_card_has_no_status(self):
        assert in_progress_status("Dairy Queen\n$12.00\nView Receipt") == ""

    def test_dasher_waiting_label(self):
        assert in_progress_status(
            "Dairy Queen\nDasher waiting for order") == "Dasher waiting for order"

    def test_doubledash_pickup_beats_generic(self):
        # Longest-match: the DoubleDash phrase wins over "picking up your order".
        assert in_progress_status(
            "Picking up your DoubleDash order") == "Picking up order"


class TestDasherExtraction:
    def test_x_is_heading(self):
        assert parse_card_text(
            "Dairy Queen\nErin is heading to you")["dasher_name"] == "Erin"

    def test_dasher_label(self):
        assert parse_card_text(
            "Dairy Queen\nDasher: Marcus")["dasher_name"] == "Marcus"

    def test_none_when_absent(self):
        assert parse_card_text(
            "Dairy Queen\n$12.00\nView Receipt")["dasher_name"] == ""
