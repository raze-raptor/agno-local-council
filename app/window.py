"""Proactive window: 03:00-09:00 Europe/London, every day (British Summer Time handled by zoneinfo, never a fixed UTC offset)."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

LON = ZoneInfo("Europe/London")
START = time(3, 0)
END = time(9, 0)


def now_london():
    return datetime.now(LON)


def _local(dt):
    return (dt or now_london()).astimezone(LON)


def in_window(dt=None):
    return START <= _local(dt).time() < END


def window_date(dt=None):
    """Local calendar date of the window this instant belongs to (the window never crosses midnight)."""
    return _local(dt).date().isoformat()


def next_window_start(dt=None):
    d = _local(dt)
    today = datetime.combine(d.date(), START, tzinfo=LON)
    return today if d < today else datetime.combine(d.date() + timedelta(days=1), START, tzinfo=LON)


def window_end(dt=None):
    d = _local(dt)
    return datetime.combine(d.date(), END, tzinfo=LON)
