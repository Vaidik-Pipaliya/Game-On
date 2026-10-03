from datetime import date, timedelta

from django.test import SimpleTestCase

from .models import Member, Membership, Plan
from .pricing import price_for

TODAY = date(2026, 10, 3)


def membership(plan, days_left=100, cancelled=False):
    """Unsaved objects are enough: the pricing function only reads fields."""
    return Membership(
        member=Member(full_name="Test"), plan=plan, start_date=TODAY - timedelta(days=200),
        end_date=TODAY + timedelta(days=days_left), cancelled=cancelled,
    )


GOLD = Plan(name="Gold", court_discount_pct=30, free_court_hours_per_month=2, shop_discount_pct=15, bar_discount_pct=10)
SILVER = Plan(name="Silver", court_discount_pct=15, free_court_hours_per_month=0, shop_discount_pct=8, bar_discount_pct=5)
JUNIOR = Plan(name="Junior", court_discount_pct=20, free_court_hours_per_month=0, shop_discount_pct=5, bar_discount_pct=0)


class PriceForTests(SimpleTestCase):
    def price(self, item, base, plan=None, **kwargs):
        m = membership(plan, **{k: kwargs.pop(k) for k in ("days_left", "cancelled") if k in kwargs}) if plan else None
        return price_for(item, base, m, today=TODAY, **kwargs)

    def test_walk_in_pays_full_price(self):
        result = self.price("court", 80000)
        self.assertEqual((result.amount_paise, result.kind, result.discount_pct), (80000, "walk_in", 0))

    def test_gold_silver_junior_court_rates(self):
        self.assertEqual(self.price("court", 80000, GOLD).amount_paise, 56000)
        self.assertEqual(self.price("court", 80000, SILVER).amount_paise, 68000)
        self.assertEqual(self.price("court", 80000, JUNIOR).amount_paise, 64000)

    def test_free_court_hour_is_zero_while_hours_remain(self):
        result = self.price("court", 80000, GOLD, free_hours_left=1)
        self.assertEqual((result.amount_paise, result.kind), (0, "free"))

    def test_free_hours_exhausted_falls_back_to_member_rate(self):
        result = self.price("court", 80000, GOLD, free_hours_left=0)
        self.assertEqual((result.amount_paise, result.kind), (56000, "member"))

    def test_plan_without_free_hours_ignores_free_hours_left(self):
        self.assertEqual(self.price("court", 80000, SILVER, free_hours_left=5).amount_paise, 68000)

    def test_free_hours_never_apply_to_shop_or_bar(self):
        self.assertEqual(self.price("shop", 100000, GOLD, free_hours_left=5).amount_paise, 85000)

    def test_shop_and_bar_use_their_own_percentages(self):
        self.assertEqual(self.price("shop", 100000, GOLD).amount_paise, 85000)
        self.assertEqual(self.price("bar", 100000, GOLD).amount_paise, 90000)
        self.assertEqual(self.price("bar", 100000, JUNIOR).amount_paise, 100000)  # 0% bar discount

    def test_discount_percentage_is_returned_for_the_bill_line(self):
        self.assertEqual(self.price("bar", 100000, GOLD).discount_pct, 10)

    def test_expired_membership_pays_walk_in_price(self):
        result = self.price("court", 80000, GOLD, days_left=-1)
        self.assertEqual((result.amount_paise, result.kind), (80000, "walk_in"))

    def test_last_day_and_expiring_still_get_benefits(self):
        self.assertEqual(self.price("court", 80000, GOLD, days_left=0).amount_paise, 56000)
        self.assertEqual(self.price("court", 80000, GOLD, days_left=7).amount_paise, 56000)

    def test_cancelled_membership_pays_walk_in_price(self):
        self.assertEqual(self.price("bar", 100000, GOLD, cancelled=True).amount_paise, 100000)

    def test_rounds_to_nearest_paisa_with_integers(self):
        # 999 * 15% = 149.85 paise discount -> 150, so 849
        self.assertEqual(self.price("shop", 999, GOLD).amount_paise, 849)
        self.assertIsInstance(self.price("shop", 999, GOLD).amount_paise, int)

    def test_zero_price_stays_zero(self):
        self.assertEqual(self.price("shop", 0, GOLD).amount_paise, 0)

    def test_unknown_item_type_and_negative_price_are_errors(self):
        with self.assertRaises(ValueError):
            self.price("spa", 100)
        with self.assertRaises(ValueError):
            self.price("shop", -1, GOLD)
