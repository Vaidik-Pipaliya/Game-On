"""Simple analytics with pandas: revenue trend, court utilisation, peak hours.

pandas is imported inside each function, not at the top: it takes ~0.6 s to load, and only the
owner dashboard needs it. Importing it at the top would add that to every cold start of every page.
"""

from datetime import timedelta

from django.conf import settings

from config.clock import local_day_bounds
from courts.models import Booking, Court
from courts.services import CLOSE_HOUR, OPEN_HOUR

from .models import Ledger
from .reports import REVENUE_KINDS


def _local(series):
    """UTC timestamps from the database -> club-local time."""
    import pandas as pd

    return pd.to_datetime(series, utc=True).dt.tz_convert(settings.TIME_ZONE)


def revenue_trend(today, days=30):
    """Net revenue per day for the last `days` days, including days with no sales (as 0)."""
    import pandas as pd

    first = today - timedelta(days=days - 1)
    start, end = local_day_bounds(first, days)
    rows = Ledger.objects.filter(kind__in=REVENUE_KINDS, created_at__gte=start, created_at__lt=end)
    all_days = pd.date_range(first, today, freq="D").date
    frame = pd.DataFrame(list(rows.values("created_at", "amount_paise")), columns=["created_at", "amount_paise"])
    if frame.empty:
        daily = pd.Series(0, index=all_days)
    else:
        frame["day"] = _local(frame["created_at"]).dt.date
        daily = frame.groupby("day")["amount_paise"].sum().reindex(all_days, fill_value=0)
    return {"labels": [d.strftime("%d %b") for d in all_days], "rupees": [int(v) / 100 for v in daily.values]}


def _bookings_frame(today, days):
    import pandas as pd

    first = today - timedelta(days=days - 1)
    start, end = local_day_bounds(first, days)
    # Whole-court rows occupy the court (including the block row of a social session).
    rows = Booking.objects.filter(
        kind=Booking.Kind.EXCLUSIVE, status__in=[Booking.Status.CONFIRMED, Booking.Status.COMPLETED],
        start__gte=start, start__lt=end,
    ).values("court__name", "start", "end")
    return pd.DataFrame(list(rows), columns=["court__name", "start", "end"])


def court_utilisation(today, days=30):
    """% of open hours each court was booked over the last `days` days."""
    import pandas as pd

    open_hours = (CLOSE_HOUR - OPEN_HOUR) * days
    courts = list(Court.objects.filter(is_active=True).order_by("name").values_list("name", flat=True))
    frame = _bookings_frame(today, days)
    booked = pd.Series(0.0, index=courts)
    if not frame.empty:
        frame["hours"] = (pd.to_datetime(frame["end"], utc=True) - pd.to_datetime(frame["start"], utc=True)).dt.total_seconds() / 3600
        booked = frame.groupby("court__name")["hours"].sum().reindex(courts, fill_value=0.0)
    return {"labels": courts, "percent": [round(h * 100 / open_hours, 1) for h in booked.values]}


def peak_hours(today, days=30):
    """How many sessions started in each hour of the day: shows when the club is busiest."""
    import pandas as pd

    hours = list(range(OPEN_HOUR, CLOSE_HOUR))
    frame = _bookings_frame(today, days)
    counts = pd.Series(0, index=hours)
    if not frame.empty:
        counts = _local(frame["start"]).dt.hour.value_counts().reindex(hours, fill_value=0)
    return {"labels": [f"{h:02d}:00" for h in hours], "sessions": [int(v) for v in counts.values]}
