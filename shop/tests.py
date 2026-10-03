from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import User
from courts.tests import Fixtures, ParallelMixin
from finance.models import Ledger, Payment

from .models import Order, Product, Variant
from .services import (
    DELIVERY_FEE_PAISE, OutOfStock, cancel_order, complete_order, place_order, restock, take_stock,
)


class ShopFixtures(Fixtures):
    def make_shop(self):
        self.make_world()  # plans: Gold (shop 0%), Silver; set a shop discount on Silver for these tests
        self.silver.shop_discount_pct = 10
        self.silver.save()
        shoes = Product.objects.create(name="Court Shoes", category="shoes", price_paise=400000)
        balls = Product.objects.create(name="Tennis Balls", category="ball", price_paise=50000)
        self.size9 = Variant.objects.create(product=shoes, size="9", stock=1, reorder_level=1)
        self.size10 = Variant.objects.create(product=shoes, size="10", stock=5, reorder_level=2)
        self.balls = Variant.objects.create(product=balls, stock=20, reorder_level=3)

    def counter(self, items, **kwargs):
        kwargs.setdefault("payment_method", "cash")
        return place_order(items=items, channel=Order.Channel.COUNTER, **kwargs)

    def online(self, items, **kwargs):
        kwargs.setdefault("customer_name", "Asha")
        return place_order(items=items, channel=Order.Channel.ONLINE, **kwargs)


def stock_of(variant):
    return Variant.objects.get(pk=variant.pk).stock


class StockTests(ShopFixtures, TestCase):
    def setUp(self):
        self.make_shop()

    def test_take_stock_subtracts(self):
        take_stock(self.size10, 2)
        self.assertEqual(stock_of(self.size10), 3)

    def test_cannot_take_more_than_on_hand_and_stock_is_unchanged(self):
        with self.assertRaisesMessage(OutOfStock, "Only 5 left"):
            take_stock(self.size10, 6)
        self.assertEqual(stock_of(self.size10), 5)

    def test_out_of_stock_message(self):
        take_stock(self.size9, 1)
        with self.assertRaisesMessage(OutOfStock, "out of stock"):
            take_stock(self.size9, 1)
        self.assertEqual(stock_of(self.size9), 0)

    def test_restock_adds_and_rejects_zero(self):
        self.assertEqual(restock(self.size9, 4).stock, 5)
        with self.assertRaises(ValidationError):
            restock(self.size9, 0)


class OrderTests(ShopFixtures, TestCase):
    def setUp(self):
        self.make_shop()

    def test_counter_sale_applies_member_discount_and_records_payment(self):
        member = self.make_member(self.silver)
        order, _ = self.counter([(self.balls, 2)], member=member, payment_method="upi")
        self.assertEqual(order.lines.get().unit_price_paise, 45000)  # 10% off ₹500
        self.assertEqual((order.total_paise, order.status, order.is_paid), (90000, "completed", True))
        row = Ledger.objects.get()
        self.assertEqual((row.source, row.method, row.amount_paise, row.reference_id), ("shop", "upi", 90000, order.pk))

    def test_counter_sale_needs_a_desk_payment_method(self):
        with self.assertRaises(ValidationError):
            self.counter([(self.balls, 1)], payment_method="online")

    def test_all_or_nothing_when_one_line_is_out_of_stock(self):
        with self.assertRaises(OutOfStock):
            self.counter([(self.balls, 2), (self.size9, 2)])
        self.assertEqual(stock_of(self.balls), 20)  # the first line's stock came back (rolled back)
        self.assertEqual((Order.objects.count(), Ledger.objects.count()), (0, 0))

    def test_same_item_twice_is_merged_into_one_line(self):
        order, _ = self.counter([(self.balls, 1), (self.balls, 2)])
        self.assertEqual(order.lines.get().quantity, 3)

    def test_low_stock_alert_fires_once_when_crossing_reorder_level(self):
        _, low = self.counter([(self.size10, 2)])  # 5 -> 3, reorder at 2: not yet
        self.assertEqual(low, [])
        _, low = self.counter([(self.size10, 1)])  # 3 -> 2: crosses
        self.assertEqual(low, [self.size10])
        _, low = self.counter([(self.size10, 1)])  # 2 -> 1: already low, no repeat alert
        self.assertEqual(low, [])

    def test_online_delivery_adds_fee_and_needs_address(self):
        with self.assertRaises(ValidationError):
            self.online([(self.balls, 1)], fulfilment="delivery")
        order, _ = self.online([(self.balls, 1)], fulfilment="delivery", delivery_address="12 MG Road")
        self.assertEqual(order.total_paise, 50000 + DELIVERY_FEE_PAISE)
        self.assertEqual((order.status, order.is_paid), ("placed", False))
        self.assertEqual(stock_of(self.balls), 19)  # online orders take from the same shelf immediately

    def test_completing_unpaid_online_order_takes_payment(self):
        order, _ = self.online([(self.balls, 1)])
        with self.assertRaises(ValidationError):
            complete_order(order)
        complete_order(order, "card")
        self.assertEqual(Ledger.objects.get().method, "card")

    def test_cancel_puts_stock_back_and_refunds_paid_order(self):
        order, _ = self.counter([(self.size10, 2)], payment_method="cash")
        order.status = Order.Status.READY  # counter sales are completed; simulate an open paid order
        order.save()
        cancel_order(order)
        self.assertEqual(stock_of(self.size10), 5)
        self.assertEqual(Ledger.objects.aggregate(t=Sum("amount_paise"))["t"], 0)
        with self.assertRaises(ValidationError):
            cancel_order(order)  # second cancel does nothing

    def test_inactive_product_cannot_be_sold(self):
        self.balls.product.is_active = False
        self.balls.product.save()
        with self.assertRaises(ValidationError):
            self.counter([(self.balls, 1)])


class LastPairRaceTests(ParallelMixin, ShopFixtures, TransactionTestCase):
    """PRD SH-03/SH-06: one pair left, counter and online at the same moment -> exactly one wins."""

    def setUp(self):
        self.make_shop()

    def test_ten_simultaneous_buyers_for_one_pair(self):
        jobs = []
        for i in range(10):
            if i % 2:
                jobs.append(lambda: place_order(items=[(self.size9, 1)], channel="counter", payment_method="cash"))
            else:
                jobs.append(lambda: place_order(items=[(self.size9, 1)], channel="online", customer_name="Online"))
        results = self.run_parallel(jobs)
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("OutOfStock"), 9)
        self.assertEqual(stock_of(self.size9), 0)
        self.assertEqual(Order.objects.count(), 1)


class ShopScreenTests(ShopFixtures, TestCase):
    def setUp(self):
        self.make_shop()
        self.shop_user = User.objects.create(username="s", email="s@example.com", role="shop_staff")
        self.customer = User.objects.create(username="c", email="c@example.com", role="member")

    def test_catalog_is_public_and_shows_stock_status(self):
        take_stock(self.size9, 1)
        response = self.client.get(reverse("shop_catalog"))
        self.assertContains(response, "Court Shoes")
        self.assertContains(response, "Out of stock")
        self.assertContains(response, "In stock")

    def test_add_to_cart_then_checkout_requires_login(self):
        self.client.post(reverse("shop_cart_add"), {"variant": self.balls.pk, "quantity": 2})
        self.assertEqual(self.client.session["cart"], {str(self.balls.pk): 2})
        self.assertEqual(self.client.get(reverse("shop_checkout")).status_code, 302)  # to login

    def test_checkout_places_order_and_empties_cart(self):
        self.client.force_login(self.customer)
        self.client.post(reverse("shop_cart_add"), {"variant": self.balls.pk, "quantity": 2})
        response = self.client.post(reverse("shop_checkout"), {
            "customer_name": "Chirag", "customer_phone": "9876500055", "fulfilment": "pickup", "payment_method": "",
        })
        self.assertRedirects(response, reverse("shop_my_orders"))
        order = Order.objects.get()
        self.assertEqual((order.channel, order.placed_by, order.total_paise), ("online", self.customer, 100000))
        self.assertEqual(self.client.session["cart"], {})

    def test_checkout_when_stock_ran_out_shows_message(self):
        self.client.force_login(self.customer)
        self.client.post(reverse("shop_cart_add"), {"variant": self.size9.pk, "quantity": 1})
        take_stock(self.size9, 1)  # someone at the counter bought it
        response = self.client.post(reverse("shop_checkout"), {
            "customer_name": "Chirag", "customer_phone": "9876500055", "fulfilment": "pickup", "payment_method": "",
        })
        self.assertContains(response, "out of stock")
        self.assertEqual(Order.objects.count(), 0)

    def test_staff_screens_need_a_shop_role(self):
        for user, code in ((self.customer, 403), (User.objects.create(username="b", email="b@e.com", role="bar_staff"), 403), (self.shop_user, 200)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("shop_counter")).status_code, code)

    def test_counter_sale_screen(self):
        self.client.force_login(self.shop_user)
        data = {"lines-TOTAL_FORMS": 5, "lines-INITIAL_FORMS": 0, "lines-0-variant": self.balls.pk,
                "lines-0-quantity": 3, "member_phone": "", "payment_method": "cash"}
        response = self.client.post(reverse("shop_counter"), data, follow=True)
        self.assertContains(response, "Sale #")
        self.assertEqual(stock_of(self.balls), 17)

    def test_order_actions_and_restock(self):
        self.client.force_login(self.shop_user)
        order, _ = self.online([(self.balls, 1)])
        self.client.post(reverse("shop_order_action", args=[order.pk, "ready"]))
        self.client.post(reverse("shop_order_action", args=[order.pk, "complete"]), {"payment_method": "upi"})
        order.refresh_from_db()
        self.assertEqual((order.status, order.is_paid), ("completed", True))
        self.client.post(reverse("shop_stock"), {"variant": self.size9.pk, "quantity": 6})
        self.assertEqual(stock_of(self.size9), 7)

    def test_customer_can_pay_only_for_own_order(self):
        order, _ = self.online([(self.balls, 1)], placed_by=self.customer, payment_method="online")
        payment = Payment.objects.create(razorpay_order_id="o1", source="shop", reference_id=order.pk, amount_paise=order.total_paise)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse("pay_page", args=[payment.pk])).status_code, 200)
        self.client.force_login(User.objects.create(username="x", email="x@example.com", role="member"))
        self.assertEqual(self.client.get(reverse("pay_page", args=[payment.pk])).status_code, 403)
