"""Load realistic fake data. Safe to run repeatedly: every row is get_or_create / update_or_create."""

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from bar.models import MenuItem, Table
from courts.models import Court, Sport
from crm.models import Lead
from members.demo_activity import already_seeded, seed_activity
from members.models import Member, Membership, Plan
from shop.models import Product, Variant

PLANS = [
    # name, fee (paise), court %, free hrs/month, shop %, bar %, junior only
    ("Gold", 1200000, 30, 2, 15, 10, False),
    ("Silver", 600000, 15, 0, 8, 5, False),
    ("Junior", 300000, 20, 0, 5, 0, True),
]

# sport -> (court names, walk-in rate in paise for one hour)
COURTS = {
    "Tennis": (["Tennis Court 1", "Tennis Court 2"], 80000),
    "Padel": (["Padel Court 1"], 120000),
    "Badminton": (["Badminton Court 1"], 40000),
    "Cricket": (["Cricket Net 1"], 150000),
}

MEMBERS = [
    ("Aarav Sharma", "9876500001", "Gold", 1990),
    ("Priya Nair", "9876500002", "Gold", 1988),
    ("Rohan Mehta", "9876500003", "Silver", 1995),
    ("Isha Patel", "9876500004", "Silver", 1992),
    ("Kabir Singh", "9876500005", "Gold", 1985),
    ("Ananya Iyer", "9876500006", "Silver", 1998),
    ("Vikram Joshi", "9876500007", "Gold", 1979),
    ("Neha Gupta", "9876500008", "Silver", 1993),
    ("Siddharth Rao", "9876500009", "Silver", 1991),
    ("Meera Kulkarni", "9876500010", "Gold", 1987),
    ("Arjun Reddy", "9876500011", "Silver", 1996),
    ("Divya Menon", "9876500012", "Gold", 1983),
]

# (guardian phone, child name, child phone, birth year)
JUNIORS = [
    ("9876500001", "Advik Sharma", "9876500101", 2013),
    ("9876500002", "Tara Nair", "9876500102", 2011),
    ("9876500005", "Zoya Singh", "9876500103", 2012),
]

PRODUCTS = [
    # name, category, price (paise), sizes
    ("Pro Staff Tennis Racket", "racket", 899900, ["One size"]),
    ("Club Tennis Racket", "racket", 349900, ["One size"]),
    ("Padel Racket Carbon", "racket", 599900, ["One size"]),
    ("Badminton Racket Lite", "racket", 249900, ["One size"]),
    ("Cricket Bat English Willow", "racket", 1299900, ["Short handle", "Long handle"]),
    ("Tennis Balls (3 pack)", "ball", 49900, ["One size"]),
    ("Shuttlecocks (12 pack)", "ball", 69900, ["One size"]),
    ("Cricket Ball Leather", "ball", 59900, ["One size"]),
    ("Padel Balls (3 pack)", "ball", 54900, ["One size"]),
    ("Court Shoes Men", "shoes", 459900, ["7", "8", "9", "10"]),
    ("Court Shoes Women", "shoes", 429900, ["5", "6", "7", "8"]),
    ("Indoor Marking Shoes", "shoes", 379900, ["7", "8", "9"]),
    ("Overgrip (3 pack)", "accessory", 29900, ["One size"]),
    ("Sports Water Bottle", "accessory", 34900, ["One size"]),
    ("Wristbands (pair)", "accessory", 14900, ["One size"]),
    ("Racket Kit Bag", "accessory", 199900, ["One size"]),
    ("Club Polo T-shirt", "apparel", 79900, ["S", "M", "L", "XL"]),
    ("Dri-fit Shorts", "apparel", 69900, ["S", "M", "L", "XL"]),
    ("Club Cap", "apparel", 39900, ["One size"]),
    ("Track Jacket", "apparel", 149900, ["M", "L", "XL"]),
]

MENU = [
    ("Masala Chai", "Drinks", 6000, "kitchen"),
    ("Cold Coffee", "Drinks", 15000, "bar"),
    ("Fresh Lime Soda", "Drinks", 8000, "bar"),
    ("Kingfisher Beer", "Drinks", 25000, "bar"),
    ("Whisky (30 ml)", "Drinks", 35000, "bar"),
    ("Mojito", "Drinks", 22000, "bar"),
    ("Veg Sandwich", "Snacks", 12000, "kitchen"),
    ("Chicken Burger", "Snacks", 18000, "kitchen"),
    ("French Fries", "Snacks", 10000, "kitchen"),
    ("Paneer Tikka", "Snacks", 22000, "kitchen"),
    ("Masala Maggi", "Snacks", 9000, "kitchen"),
    ("Butter Chicken Meal", "Meals", 32000, "kitchen"),
    ("Veg Thali", "Meals", 25000, "kitchen"),
]

LEADS = [
    ("Rahul Verma", "9811100001", "Tennis", "new"),
    ("Sneha Kapoor", "9811100002", "Padel", "contacted"),
    ("Manish Desai", "9811100003", "Badminton", "quoted"),
    ("Pooja Shetty", "9811100004", "Cricket", "won"),
    ("Anil Bhatt", "9811100005", "Tennis", "lost"),
]


class Command(BaseCommand):
    help = "Load demo plans, members, courts, shop, bar menu, leads and 30 days of activity."

    def handle(self, *args, **options):
        today = timezone.localdate()
        plans = {}
        for name, fee, court_pct, free_hrs, shop_pct, bar_pct, junior in PLANS:
            plans[name], _ = Plan.objects.update_or_create(
                name=name,
                defaults=dict(
                    price_paise=fee,
                    court_discount_pct=court_pct,
                    free_court_hours_per_month=free_hrs,
                    shop_discount_pct=shop_pct,
                    bar_discount_pct=bar_pct,
                    junior_only=junior,
                ),
            )

        for i, (name, phone, plan, born) in enumerate(MEMBERS):
            member = self._member(name, phone, born)
            # Spread end dates so the demo shows expired, expiring-soon and healthy memberships.
            end = today + timedelta(days=[-10, 5, 14, 90, 200, 300][i % 6])
            self._membership(member, plans[plan], end)

        for guardian_phone, name, phone, born in JUNIORS:
            guardian = Member.objects.get(phone=guardian_phone)
            child = self._member(name, phone, born, guardian=guardian)
            self._membership(child, plans["Junior"], today + timedelta(days=120))

        for sport_name, (court_names, rate) in COURTS.items():
            sport, _ = Sport.objects.get_or_create(name=sport_name)
            for court_name in court_names:
                Court.objects.update_or_create(
                    sport=sport, name=court_name, defaults={"walk_in_rate_paise": rate}
                )

        for i, (name, category, price, sizes) in enumerate(PRODUCTS):
            product, _ = Product.objects.update_or_create(
                name=name, defaults={"category": category, "price_paise": price}
            )
            for j, size in enumerate(sizes):
                # A few variants start at 0-2 so the low-stock alert has something to show.
                stock = [12, 8, 2, 0, 15][(i + j) % 5]
                Variant.objects.get_or_create(product=product, size=size, defaults={"stock": stock})

        for name, category, price, station in MENU:
            MenuItem.objects.update_or_create(
                name=name, defaults={"category": category, "price_paise": price, "station": station}
            )
        for n in range(1, 9):
            Table.objects.get_or_create(label=f"T{n}")

        for name, phone, sport, status in LEADS:
            Lead.objects.get_or_create(
                phone=phone,
                defaults={
                    "name": name,
                    "sport_interest": sport,
                    "status": status,
                    "message": f"Interested in {sport} memberships.",
                    "follow_up_date": today + timedelta(days=2),
                },
            )

        if already_seeded():
            self.stdout.write("Activity history already present; not adding it again.")
        else:
            seed_activity(today)
            self.stdout.write("Added 30 days of bookings, sales, bar tabs, fees, payroll and invoices.")
        self.stdout.write(self.style.SUCCESS("Demo data loaded."))

    def _member(self, name, phone, born_year, guardian=None):
        member, _ = Member.objects.update_or_create(
            phone=phone,
            defaults={
                "full_name": name,
                "email": f"{name.split()[0].lower()}{phone[-3:]}@example.com",
                "date_of_birth": date(born_year, 6, 15),
                "emergency_contact": "Family - 9000000000",
                "guardian": guardian,
                "whatsapp_opt_in": True,
            },
        )
        return member

    def _membership(self, member, plan, end_date):
        Membership.objects.update_or_create(
            member=member,
            defaults={"plan": plan, "start_date": end_date - timedelta(days=plan.duration_days), "end_date": end_date},
        )
