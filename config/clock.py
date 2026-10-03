"""Club-local calendar helpers. Business days are Asia/Kolkata days, while the database stores UTC."""

from datetime import datetime, time, timedelta

from django.utils import timezone


def local_day_bounds(first_day, days=1):
    """[start, end) covering `days` club-local calendar days from first_day, as aware datetimes."""
    tz = timezone.get_current_timezone()
    return (
        datetime.combine(first_day, time.min, tzinfo=tz),
        datetime.combine(first_day + timedelta(days=days), time.min, tzinfo=tz),
    )
