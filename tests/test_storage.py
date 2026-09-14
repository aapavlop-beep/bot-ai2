import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from app.storage import Store, decimal_odds, money


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.set_bank(1, 1000)

    def tearDown(self):
        self.store.close()

    def rec(self, **updates):
        return self.store.save_recommendations(1, "test", [dict(id="stable", event_name="A — B", market="П1", odds=2.5, stake=100, **updates)])[0]

    def test_bank_and_cart_multi_confirm_atomic(self):
        a, b = self.rec(), self.rec()
        self.store.add_to_cart(1, a["id"])
        self.store.add_to_cart(1, b["id"])
        self.store.edit_cart(1, b["id"], 200, 3)
        placed = self.store.confirm_cart(1)
        self.assertEqual(len(placed), 2)
        self.assertEqual(self.store.get_bank(1)["available"], 700)
        self.assertEqual(self.store.get_bank(1)["exposure"], 300)
        self.assertEqual(self.store.cart(1), [])
        with self.assertRaises(ValueError):
            self.store.confirm_cart(1)
        self.assertEqual(len(self.store.history(1)), 2)

    def test_insufficient_total_rolls_back_everything(self):
        a, b = self.rec(), self.rec()
        for rec in (a, b):
            self.store.add_to_cart(1, rec["id"])
            self.store.edit_cart(1, rec["id"], 600, 2)
        with self.assertRaises(ValueError):
            self.store.confirm_cart(1)
        self.assertEqual(self.store.history(1), [])
        self.assertEqual(len(self.store.cart(1)), 2)
        self.assertEqual(self.store.get_bank(1)["available"], 1000)

    def test_user_isolation(self):
        rec = self.rec()
        self.store.set_bank(2, 1000)
        self.assertIsNone(self.store.get_recommendation(2, rec["id"]))
        with self.assertRaises(ValueError):
            self.store.add_to_cart(2, rec["id"])
        self.store.add_to_cart(1, rec["id"])
        self.assertEqual(self.store.cart(2), [])
        bet = self.store.confirm_cart(1)[0]
        with self.assertRaises(ValueError):
            self.store.settle(2, bet["id"], "won")
        self.assertEqual(self.store.history(1)[0]["status"], "pending")

    def test_results_bank_snapshots_and_no_duplicate_settlement(self):
        rec = self.rec()
        self.store.add_to_cart(1, rec["id"])
        bet = self.store.confirm_cart(1)[0]
        self.store.set_bank(1, 900)
        self.assertEqual(self.store.get_bank(1)["available"], 900)
        self.store.settle(1, bet["id"], "won")
        self.assertEqual(self.store.get_bank(1)["available"], 1150)
        self.assertEqual(self.store.get_bank(1)["pnl"], 150)
        with self.assertRaises(ValueError):
            self.store.settle(1, bet["id"], "won")
        with self.assertRaises(ValueError):
            self.store.add_to_cart(1, rec["id"])
        self.assertEqual(self.store.get_bank(1)["available"], 1150)

    def test_daily_bank_required_and_cart_reconfirmation(self):
        rec = self.rec()
        self.store.add_to_cart(1, rec["id"])
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        self.store.now = lambda: tomorrow
        self.assertIsNone(self.store.get_bank(1))
        with self.assertRaises(ValueError):
            self.store.confirm_cart(1)
        self.store.set_bank(1, 1000)
        with self.assertRaises(ValueError):
            self.store.confirm_cart(1)
        self.store.edit_cart(1, rec["id"], 50, 2)
        self.assertEqual(len(self.store.confirm_cart(1)), 1)

    def test_snapshot_history_and_current_search_do_not_mix(self):
        old = self.rec()
        new = self.store.save_recommendations(1, "latest", [{"id": "new", "stake": 0, "odds": None}])[0]
        self.assertEqual([item["id"] for item in self.store.latest_recommendations(1)], [new["id"]])
        self.assertEqual(len(self.store.list_recommendations(1)), 2)
        self.assertEqual(self.store.get_recommendation(1, old["id"])["odds"], 2.5)

    def test_restart_preserves_bank_cart_and_history(self):
        path = Path(__file__).resolve().parent / f"restart-{uuid.uuid4().hex}.db"
        try:
            store = Store(str(path))
            store.set_bank(5, 1500)
            item = store.save_recommendations(5, "s", [{"stake": 50, "odds": 2}])[0]
            store.add_to_cart(5, item["id"])
            store.close()
            reopened = Store(str(path))
            self.assertEqual(reopened.get_bank(5)["available"], 1500)
            self.assertEqual(len(reopened.cart(5)), 1)
            reopened.confirm_cart(5)
            self.assertEqual(len(reopened.history(5)), 1)
            reopened.close()
        finally:
            path.unlink(missing_ok=True)

    def test_void_and_loss_accounting(self):
        for status, expected in (("void", 1000), ("lost", 900)):
            rec = self.rec()
            self.store.add_to_cart(1, rec["id"])
            bet = self.store.confirm_cart(1)[0]
            self.store.settle(1, bet["id"], status)
            self.assertEqual(self.store.get_bank(1)["available"], expected)

    def test_nonfinite_negative_and_invalid_values_rejected(self):
        for value in ("nan", "inf", "-inf", 0, -1, "", None, .001):
            with self.assertRaises(ValueError):
                money(value)
        for value in ("nan", "inf", 1, -1, None):
            with self.assertRaises(ValueError):
                decimal_odds(value)
        self.assertEqual(money("10,015"), 1002)


if __name__ == "__main__":
    unittest.main()
