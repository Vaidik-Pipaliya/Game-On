from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog, User
from courts.tests import Fixtures
from finance.models import Ledger

from .models import MenuItem, Shift, Tab, TabLine, Table
from .services import (
    add_item, attach_member, bill_for, day_report, end_shift, kitchen_tickets, mark_line_ready, open_tab,
    settle_tab, start_shift, void_empty_tab,
)
from .views import _rupees_to_paise


class BarFixtures(Fixtures):
    def make_bar(self):
        self.make_world()
        self.gold.bar_discount_pct = 10
        self.gold.save()
        self.t1 = Table.objects.create(label="T1")
        self.beer = MenuItem.objects.create(name="Beer", category="Drinks", price_paise=25000, station="bar")
        self.burger = MenuItem.objects.create(name="Burger", category="Snacks", price_paise=50000, station="kitchen")


class TabTests(BarFixtures, TestCase):
    def setUp(self):
        self.make_bar()

    def test_only_one_open_tab_per_table_enforced_by_database(self):
        open_tab(table=self.t1)
        with self.assertRaisesMessage(ValidationError, "already has an open tab"):
            open_tab(table=self.t1)

    def test_table_is_free_again_after_settling(self):
        tab = open_tab(table=self.t1)
        add_item(tab, self.beer)
        settle_tab(tab, [("cash", 25000)])
        open_tab(table=self.t1)  # no error

    def test_gold_member_discount_is_an_automatic_visible_line(self):
        # PRD BR-05: ₹1,000 of items on a Gold tab shows "Gold member discount" without staff action.
        tab = open_tab(table=self.t1, member=self.make_member(self.gold))
        add_item(tab, self.burger, 2)
        bill = bill_for(tab)
        self.assertEqual((bill.subtotal_paise, bill.discount_paise, bill.total_paise), (100000, 10000, 90000))
        self.assertEqual(bill.discount_label, "Gold member discount -10%")

    def test_attaching_member_later_applies_discount(self):
        tab = open_tab(table=self.t1)
        add_item(tab, self.burger, 2)
        self.assertEqual(bill_for(tab).discount_paise, 0)
        tab = attach_member(tab, self.make_member(self.gold))  # the service returns the updated tab
        self.assertEqual(bill_for(tab).total_paise, 90000)

    def test_price_is_frozen_when_ordered(self):
        tab = open_tab(table=self.t1)
        add_item(tab, self.beer)
        self.beer.price_paise = 99900
        self.beer.save()
        self.assertEqual(bill_for(tab).total_paise, 25000)

    def test_unavailable_item_cannot_be_ordered(self):
        self.beer.is_available = False
        self.beer.save()
        with self.assertRaises(ValidationError):
            add_item(open_tab(table=self.t1), self.beer)

    def test_split_payment_writes_one_ledger_row_per_method(self):
        tab = open_tab(table=self.t1)
        add_item(tab, self.burger, 2)
        settle_tab(tab, [("cash", 40000), ("upi", 60000), ("card", 0)])
        self.assertEqual(sorted(Ledger.objects.values_list("method", "amount_paise")), [("cash", 40000), ("upi", 60000)])
        tab.refresh_from_db()
        self.assertEqual((tab.status, tab.total_paise), ("paid", 100000))

    def test_payments_must_add_up_to_the_bill(self):
        tab = open_tab(table=self.t1)
        add_item(tab, self.burger)
        with self.assertRaisesMessage(ValidationError, "but the bill is"):
            settle_tab(tab, [("cash", 40000)])
        self.assertEqual(Ledger.objects.count(), 0)

    def test_closed_tab_cannot_be_changed_or_settled_again(self):
        tab = open_tab(table=self.t1)
        add_item(tab, self.beer)
        settle_tab(tab, [("cash", 25000)])
        for action in (lambda: add_item(tab, self.beer), lambda: settle_tab(tab, [("cash", 25000)])):
            with self.assertRaises(ValidationError):
                action()
        self.assertEqual(Ledger.objects.count(), 1)

    def test_only_empty_tabs_can_be_voided(self):
        tab = open_tab(customer_name="Ravi")
        add_item(tab, self.beer)
        with self.assertRaises(ValidationError):
            void_empty_tab(tab)
        self.assertEqual(void_empty_tab(open_tab(customer_name="Oops")).status, "void")

    def test_tab_needs_table_member_or_name(self):
        with self.assertRaises(ValidationError):
            open_tab()


class KitchenTests(BarFixtures, TestCase):
    def setUp(self):
        self.make_bar()

    def test_tickets_are_routed_by_station_and_leave_when_ready(self):
        tab = open_tab(table=self.t1)
        add_item(tab, self.burger)
        beer_line = add_item(tab, self.beer)
        self.assertEqual([l.menu_item for l in kitchen_tickets("kitchen")], [self.burger])
        self.assertEqual([l.menu_item for l in kitchen_tickets("bar")], [self.beer])
        mark_line_ready(beer_line)
        self.assertEqual(list(kitchen_tickets("bar")), [])
        self.assertEqual(TabLine.objects.get(pk=beer_line.pk).progress, "ready")


class ShiftAndReportTests(BarFixtures, TestCase):
    def setUp(self):
        self.make_bar()
        self.staff = User.objects.create(username="b", email="b@example.com", role="bar_staff")

    def test_shift_cash_difference(self):
        shift = start_shift(self.staff, 100000)  # ₹1,000 float
        tab = open_tab(table=self.t1)
        add_item(tab, self.burger)
        settle_tab(tab, [("cash", 30000), ("upi", 20000)])
        summary = end_shift(shift, 125000, now=timezone.now() + timedelta(seconds=1))  # counted ₹1,250
        self.assertEqual((summary.cash_sales_paise, summary.expected_cash_paise, summary.difference_paise), (30000, 130000, -5000))
        self.assertEqual(summary.sales_paise, 50000)

    def test_one_open_shift_per_person(self):
        start_shift(self.staff, 0)
        with self.assertRaises(ValidationError):
            start_shift(self.staff, 0)

    def test_day_report_totals_by_method_discounts_and_open_tabs(self):
        gold_tab = open_tab(table=self.t1, member=self.make_member(self.gold))
        add_item(gold_tab, self.burger, 2)
        settle_tab(gold_tab, [("card", 90000)])
        other = open_tab(customer_name="Ravi")
        add_item(other, self.beer)
        report = day_report(timezone.localdate())
        self.assertEqual((report["by_method"]["card"], report["total"], report["discounts"]), (90000, 90000, 10000))
        self.assertEqual(report["tabs_closed"], 1)
        self.assertEqual([(t.pk, b.total_paise) for t, b in report["open_tabs"]], [(other.pk, 25000)])
        self.assertEqual(Ledger.objects.filter(source="bar").aggregate(t=Sum("amount_paise"))["t"], report["total"])


class BarScreenTests(BarFixtures, TestCase):
    def setUp(self):
        self.make_bar()
        self.client.force_login(User.objects.create(username="b", email="b@example.com", role="bar_staff"))

    def test_rupee_input_parsing(self):
        self.assertEqual([_rupees_to_paise(v) for v in ("450", "450.5", "1,250.75", "")], [45000, 45050, 125075, 0])
        with self.assertRaises(ValueError):
            _rupees_to_paise("abc")

    def test_full_flow_open_order_attach_member_settle(self):
        member = self.make_member(self.gold)
        self.client.post(reverse("bar_tab_open"), {"table": self.t1.pk})
        tab = Tab.objects.get()
        self.client.post(reverse("bar_tab_action", args=[tab.pk, "add"]), {"item": self.burger.pk})
        self.client.post(reverse("bar_tab_action", args=[tab.pk, "add"]), {"item": self.burger.pk})
        self.client.post(reverse("bar_tab_action", args=[tab.pk, "member"]), {"member_phone": member.phone})
        page = self.client.get(reverse("bar_tab", args=[tab.pk]))
        self.assertContains(page, "Gold member discount -10%")
        self.assertContains(page, 'value="900"')  # prefilled amount in rupees
        self.client.post(reverse("bar_tab_action", args=[tab.pk, "settle"]), {
            "method_0": "cash", "amount_0": "500", "method_1": "upi", "amount_1": "400", "method_2": "card", "amount_2": "",
        })
        tab.refresh_from_db()
        self.assertEqual(tab.status, "paid")

    def test_kitchen_screen_auto_refreshes_and_lists_tickets(self):
        add_item(open_tab(table=self.t1), self.burger)
        response = self.client.get(reverse("bar_kitchen"), {"station": "kitchen"})
        self.assertContains(response, 'http-equiv="refresh"')
        self.assertContains(response, "Burger")

    def test_front_desk_cannot_use_the_bar(self):
        self.client.force_login(User.objects.create(username="d", email="d@example.com", role="front_desk"))
        self.assertEqual(self.client.get(reverse("bar_tables")).status_code, 403)

    def test_shift_and_report_pages(self):
        self.client.post(reverse("bar_shift"), {"cash": "1000"})
        self.assertTrue(Shift.objects.filter(ended_at__isnull=True).exists())
        self.assertEqual(self.client.get(reverse("bar_day_report")).status_code, 200)


class MenuTests(BarFixtures, TestCase):
    def setUp(self):
        self.make_bar()
        self.staff = User.objects.create(username="b", email="b@example.com", role="bar_staff")
        self.client.force_login(self.staff)

    def test_staff_add_an_item_with_a_rupee_price(self):
        response = self.client.post(reverse("bar_menu_new"), {
            "name": "Cold Coffee", "category": "Coffee", "price_rupees": "149.50", "station": "bar", "is_available": "on",
        })
        self.assertRedirects(response, reverse("bar_menu"))
        item = MenuItem.objects.get(name="Cold Coffee")
        self.assertEqual(item.price_paise, 14950)
        self.assertTrue(AuditLog.objects.filter(action="menu.add", target_id=item.pk).exists())

    def test_duplicate_name_is_refused(self):
        response = self.client.post(reverse("bar_menu_new"), {
            "name": "beer", "category": "Drinks", "price_rupees": "200", "station": "bar",
        })
        self.assertContains(response, "already a menu item with this name")

    def test_price_change_is_audited_and_open_tabs_keep_their_price(self):
        tab = open_tab(table=self.t1)
        add_item(tab, self.beer)
        self.client.post(reverse("bar_menu_edit", args=[self.beer.pk]), {
            "name": "Beer", "category": "Drinks", "price_rupees": "300", "station": "bar", "is_available": "on",
        })
        self.beer.refresh_from_db()
        self.assertEqual(self.beer.price_paise, 30000)
        self.assertEqual(bill_for(tab).subtotal_paise, 25000)  # ordered before the change
        log = AuditLog.objects.get(action="menu.price")
        self.assertEqual((log.details["before"], log.details["after"]), (25000, 30000))

    def test_sold_out_item_leaves_the_tab_screen_but_shows_publicly_as_sold_out(self):
        self.client.post(reverse("bar_menu_toggle", args=[self.burger.pk]))
        self.burger.refresh_from_db()
        self.assertFalse(self.burger.is_available)
        tab = open_tab(table=self.t1)
        page = self.client.get(reverse("bar_tab", args=[tab.pk]))
        self.assertNotContains(page, f'name="item" value="{self.burger.pk}"')
        self.assertContains(page, f'name="item" value="{self.beer.pk}"')
        self.client.logout()
        public = self.client.get(reverse("cafe"))
        self.assertContains(public, "Burger")
        self.assertContains(public, "Sold out today")
        self.assertContains(public, "Gold 10%")

    def test_front_desk_cannot_edit_the_menu(self):
        self.client.force_login(User.objects.create(username="d", email="d@example.com", role="front_desk"))
        self.assertEqual(self.client.get(reverse("bar_menu")).status_code, 403)
        self.assertEqual(self.client.post(reverse("bar_menu_toggle", args=[self.beer.pk])).status_code, 403)
