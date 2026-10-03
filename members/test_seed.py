from io import StringIO

from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase

from courts.models import Booking
from finance.models import Ledger
from members.models import Member
from shop.models import Order


def total(queryset, field):
    return queryset.aggregate(t=Sum(field))["t"] or 0


class SeedDemoTests(TestCase):
    def test_seed_twice_adds_nothing_the_second_time(self):
        call_command("seed_demo", stdout=StringIO())
        counts = (Member.objects.count(), Booking.objects.count(), Ledger.objects.count())
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual((Member.objects.count(), Booking.objects.count(), Ledger.objects.count()), counts)
        self.assertGreater(counts[1], 300)  # a month of bookings

    def test_demo_money_reconciles_with_the_ledger(self):
        call_command("seed_demo", stdout=StringIO())
        paid_bookings = Booking.objects.filter(is_paid=True).exclude(status="cancelled")
        self.assertEqual(total(Ledger.objects.filter(source="court"), "amount_paise"), total(paid_bookings, "price_paise"))
        paid_orders = Order.objects.filter(is_paid=True).exclude(status="cancelled")
        self.assertEqual(total(Ledger.objects.filter(source="shop"), "amount_paise"), total(paid_orders, "total_paise"))
