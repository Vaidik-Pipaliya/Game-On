"""The one pricing function. Courts, shop and bar all call price_for(), so a discount rule lives in one place."""

from dataclasses import dataclass

from django.utils import timezone

# Which Plan field holds the discount % for each kind of item.
DISCOUNT_FIELD = {
    "court": "court_discount_pct",
    "shop": "shop_discount_pct",
    "bar": "bar_discount_pct",
}


@dataclass(frozen=True)
class Price:
    amount_paise: int
    kind: str  # "free", "member" or "walk_in"
    discount_pct: int  # shown to staff as "Gold member discount -10%"


def price_for(item_type, base_paise, membership=None, *, free_hours_left=0, today=None):
    """Final price in paise for one item.

    `base_paise` is the walk-in price. `membership` is the member's latest membership
    (or None for a walk-in). Only active or expiring memberships get benefits, so an
    expired member automatically pays the walk-in price.
    `free_hours_left` is how many free court hours the member still has this month;
    the booking code works that out and passes it in, which keeps this function pure.
    """
    if item_type not in DISCOUNT_FIELD:
        raise ValueError(f"Unknown item type: {item_type}")
    if base_paise < 0:
        raise ValueError("Price cannot be negative.")

    today = today or timezone.localdate()
    if membership is None or membership.status_on(today) not in ("active", "expiring"):
        return Price(base_paise, "walk_in", 0)

    plan = membership.plan
    if item_type == "court" and free_hours_left > 0 and plan.free_court_hours_per_month > 0:
        return Price(0, "free", 100)

    pct = getattr(plan, DISCOUNT_FIELD[item_type])
    # Integer maths only (no floats with money); +50 rounds half up to the nearest paisa.
    discount = (base_paise * pct + 50) // 100
    return Price(base_paise - discount, "member", pct)
