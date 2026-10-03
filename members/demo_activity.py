"""30 days of believable club activity for demos: bookings, shop sales, bar tabs, fees, payroll.

Every money event writes a ledger row dated when it happened, so the owner dashboard, the
daily reports and the CSV exports all agree. Runs once: a marker note in the ledger guards it.
"""

import random
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from bar.models import MenuItem, Tab, TabLine, Table
from bar.services import add_item, open_tab
from courts.models import Booking, Court
from courts.services import create_social_session
from crm.models import Lead
from finance.invoicing import create_invoice, mark_invoice_paid
from finance.models import Ledger
from finance.services import record_payment, record_refund
from shop.models import Order, OrderLine, Variant
from shop.services import place_order
from staffing.models import Employee, LeaveRequest
from staffing.services import pay_payroll, run_payroll

from .models import Member, Membership, Plan
from .pricing import price_for

MARKER = "Demo history"
METHODS = ["cash", "upi", "upi", "card"]  # UPI is the most common way to pay in India
# Hour -> how popular it is. Mornings before work and evenings after work are the peaks.
HOUR_WEIGHTS = {6: 5, 7: 6, 8: 4, 9: 2, 10: 1, 11: 1, 12: 1, 13: 1, 14: 1, 15: 2, 16: 3, 17: 6, 18: 8, 19: 8, 20: 6, 21: 3}
NEW_MEMBERS = [("Harsh Mehta", "9876500201", "Gold"), ("Riya Desai", "9876500202", "Silver"),
               ("Nikhil Bose", "9876500203", "Silver"), ("Sana Qureshi", "9876500204", "Gold")]
EMPLOYEES = [("Meena Pillai", "Front desk lead", 3200000), ("Imran Khan", "Bar manager", 3000000),
             ("Deepak Yadav", "Shop assistant", 2200000), ("Lakshmi Rao", "Cafe cook", 2400000),
             ("Arjun Thakur", "Groundsman", 1800000)]


def _at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=timezone.get_current_timezone())


def _busy_hours(rng, count):
    """Pick `count` distinct hours, favouring popular ones."""
    ranked = sorted(HOUR_WEIGHTS, key=lambda h: rng.random() ** (1 / HOUR_WEIGHTS[h]), reverse=True)
    return sorted(ranked[:count])


def already_seeded():
    return Ledger.objects.filter(note__startswith=MARKER).exists()


@transaction.atomic
def seed_activity(today, seed=42):
    rng = random.Random(seed)  # same data every time, so demos are repeatable
    members = list(Member.objects.prefetch_related("memberships__plan"))
    courts = list(Court.objects.filter(is_active=True))
    variants = list(Variant.objects.select_related("product"))
    menu = list(MenuItem.objects.all())

    _new_member_fees(today, rng)
    for days_ago in range(30, 0, -1):
        day = today - timedelta(days=days_ago)
        _court_day(day, courts, members, rng)
        _shop_day(day, variants, members, rng)
        _bar_day(day, menu, members, rng)
    _today_and_upcoming(today, courts, members, rng, variants, menu)
    _staff_and_invoices(today)


def _new_member_fees(today, rng):
    for i, (name, phone, plan_name) in enumerate(NEW_MEMBERS):
        joined = today - timedelta(days=4 + i * 6)
        plan = Plan.objects.get(name=plan_name)
        member, _ = Member.objects.get_or_create(phone=phone, defaults={
            "full_name": name, "email": f"{name.split()[0].lower()}{phone[-3:]}@example.com",
            "date_of_birth": joined.replace(year=1992), "whatsapp_opt_in": True,
        })
        membership = Membership.objects.create(member=member, plan=plan, start_date=joined,
                                               end_date=joined + timedelta(days=plan.duration_days))
        record_payment(source="membership", method=rng.choice(METHODS), amount_paise=plan.price_paise,
                       reference_id=membership.pk, note=f"{MARKER}: {plan.name} membership", at=_at(joined, 11))


def _court_day(day, courts, members, rng):
    weekend = day.weekday() >= 5
    per_member = {}
    for court in courts:
        for hour in _busy_hours(rng, rng.randint(4, 9) if weekend else rng.randint(3, 7)):
            start = _at(day, hour)
            member = rng.choice(members) if rng.random() < 0.7 else None
            if member and per_member.get(member.pk, 0) >= 2:
                member = None  # respect the 2-per-day rule in the history too
            membership = member.current_membership if member else None
            price = price_for("court", court.walk_in_rate_paise, membership, today=day).amount_paise
            method = rng.choice(METHODS) if price else ""
            cancelled = rng.random() < 0.03
            booking = Booking.objects.create(
                court=court, member=member, guest_name="" if member else "Walk-in guest",
                guest_phone="" if member else "9000000000", start=start, end=start + timedelta(hours=1),
                status=Booking.Status.CANCELLED if cancelled else Booking.Status.COMPLETED,
                price_paise=price, is_paid=bool(price), payment_method=method,
            )
            if member:
                per_member[member.pk] = per_member.get(member.pk, 0) + 1
            if price:
                record_payment(source="court", method=method, amount_paise=price, reference_id=booking.pk,
                               note=f"{MARKER}: court booking", at=start - timedelta(days=1))
                if cancelled:
                    record_refund(source="court", method=method, amount_paise=price, reference_id=booking.pk,
                                  note=f"{MARKER}: cancelled booking", at=start - timedelta(hours=30))


def _shop_day(day, variants, members, rng):
    for _ in range(rng.randint(1, 4)):
        when = _at(day, rng.randint(8, 20), rng.choice([0, 15, 30, 45]))
        member = rng.choice(members) if rng.random() < 0.5 else None
        membership = member.current_membership if member else None
        method = rng.choice(METHODS)
        order = Order.objects.create(member=member, channel="counter", status="completed", is_paid=True, payment_method=method)
        total = 0
        for variant in rng.sample(variants, rng.randint(1, 2)):
            quantity = rng.randint(1, 2)
            unit = price_for("shop", variant.product.price_paise, membership, today=day).amount_paise
            OrderLine.objects.create(order=order, variant=variant, quantity=quantity, unit_price_paise=unit)
            total += unit * quantity
        Order.objects.filter(pk=order.pk).update(total_paise=total, created_at=when)  # orders aren't append-only
        record_payment(source="shop", method=method, amount_paise=total, reference_id=order.pk,
                       note=f"{MARKER}: shop sale", at=when)


def _bar_day(day, menu, members, rng):
    weekend = day.weekday() >= 5
    for _ in range(rng.randint(6, 12) if weekend else rng.randint(3, 8)):
        opened = _at(day, rng.choice([8, 12, 13, 18, 19, 20, 21]), rng.choice([0, 20, 40]))
        member = rng.choice(members) if rng.random() < 0.6 else None
        membership = member.current_membership if member else None
        tab = Tab.objects.create(member=member, customer_name="" if member else "Guest", status="paid")
        subtotal = 0
        for item in rng.sample(menu, rng.randint(1, 4)):
            quantity = rng.randint(1, 3)
            TabLine.objects.create(tab=tab, menu_item=item, quantity=quantity, unit_price_paise=item.price_paise, progress="served")
            subtotal += item.price_paise * quantity
        total = price_for("bar", subtotal, membership, today=day).amount_paise
        closed = opened + timedelta(minutes=rng.randint(30, 120))
        Tab.objects.filter(pk=tab.pk).update(opened_at=opened, closed_at=closed, total_paise=total, discount_paise=subtotal - total)
        record_payment(source="bar", method=rng.choice(METHODS), amount_paise=total, reference_id=tab.pk,
                       note=f"{MARKER}: bar tab", at=closed)


def _today_and_upcoming(today, courts, members, rng, variants, menu):
    now = timezone.now()
    for day in (today, today + timedelta(days=1)):
        for court in courts[:3]:
            for hour in _busy_hours(rng, 3):
                start = _at(day, hour)
                if start <= now:
                    continue
                member = rng.choice(members)
                price = price_for("court", court.walk_in_rate_paise, member.current_membership, today=day).amount_paise
                try:
                    with transaction.atomic():  # savepoint: a slot you already booked by hand is simply skipped
                        Booking.objects.create(court=court, member=member, start=start, end=start + timedelta(hours=1),
                                               price_paise=price, is_paid=price == 0)  # unpaid ones show under "amounts owed"
                except IntegrityError:
                    continue
    next_friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    session_court = Court.objects.filter(name="Tennis Court 2").first() or courts[0]
    try:
        session = create_social_session(court=session_court, start=_at(next_friday, 19), capacity=12, price_per_player_paise=20000)
    except ValidationError:
        session = None  # that slot is already booked in the demo data; skip the social session
    for member in members[:4] if session else []:
        seat = Booking.objects.create(court=session_court, member=member, start=session.start, end=session.end, kind="social",
                                      social_session=session, price_paise=20000, is_paid=True, payment_method="upi")
        record_payment(source="court", method="upi", amount_paise=20000, reference_id=seat.pk, note=f"{MARKER}: social seat")
    tab = open_tab(table=Table.objects.order_by("label").first(), member=members[0])
    for item in menu[:3]:
        add_item(tab, item)  # appears on the kitchen and bar screens
    in_stock = next(v for v in variants if v.stock > 5)
    place_order(items=[(in_stock, 1)], channel="online", member=members[1], customer_phone=members[1].phone)
    Lead.objects.filter(status="new").update(follow_up_date=today - timedelta(days=1))  # one overdue follow-up


def _staff_and_invoices(today):
    for name, title, salary in EMPLOYEES:
        Employee.objects.get_or_create(full_name=name, defaults={"job_title": title, "monthly_salary_paise": salary,
                                                                 "joined_on": today.replace(year=today.year - 1)})
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    for payroll in run_payroll(last_month):
        pay_payroll(payroll, "online")
    LeaveRequest.objects.create(employee=Employee.objects.get(full_name="Imran Khan"),
                                from_date=today + timedelta(days=10), to_date=today + timedelta(days=11), reason="Family wedding")
    paid = create_invoice(customer_name="Infotech Solutions Pvt Ltd", customer_gstin="24AABCI1234F1Z5",
                          description="Corporate membership, 5 staff, 12 months", amount_paise=4500000, gst_pct=18,
                          issued_on=today - timedelta(days=12))
    mark_invoice_paid(paid, "card")
    create_invoice(customer_name="Ahmedabad Tennis Academy", description="Court hire, 8 sessions",
                   amount_paise=640000, gst_pct=18, issued_on=today - timedelta(days=3), source="court")
