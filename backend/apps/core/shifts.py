"""
Plant shift calendar (brief p.3, p.10).

Day shift 09:00-21:00, night shift 21:00-09:00 next day. A night shift keeps
its *shift date* (the date it started) across midnight so 02:00 on the 15th
belongs to the night shift of the 14th.
"""
import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

K = settings.KANTAM
PLANT_TZ = ZoneInfo(K["PLANT_TZ"])


@dataclass(frozen=True)
class ShiftSlot:
    shift_date: datetime.date
    shift: str  # "day" | "night"

    @property
    def start(self):
        hour = K["DAY_SHIFT_START_HOUR"] if self.shift == "day" else K["NIGHT_SHIFT_START_HOUR"]
        return datetime.datetime.combine(self.shift_date, datetime.time(hour), tzinfo=PLANT_TZ)

    @property
    def end(self):
        return self.start + datetime.timedelta(hours=K["SHIFT_HOURS"])

    @property
    def label(self):
        return f"{self.shift_date:%d-%b-%Y} {self.shift.title()}"

    def next(self):
        if self.shift == "day":
            return ShiftSlot(self.shift_date, "night")
        return ShiftSlot(self.shift_date + datetime.timedelta(days=1), "day")


def shift_for(moment=None):
    moment = timezone.localtime(moment or timezone.now(), PLANT_TZ)
    day_start = K["DAY_SHIFT_START_HOUR"]
    night_start = K["NIGHT_SHIFT_START_HOUR"]
    if day_start <= moment.hour < night_start:
        return ShiftSlot(moment.date(), "day")
    if moment.hour >= night_start:
        return ShiftSlot(moment.date(), "night")
    return ShiftSlot(moment.date() - datetime.timedelta(days=1), "night")


def is_shift_closed(shift_date, shift, site=None, now=None):
    """Closed explicitly by a supervisor, or automatically after end + grace."""
    from .models import ShiftLock

    now = now or timezone.now()
    slot = ShiftSlot(shift_date, shift)
    if now >= slot.end + datetime.timedelta(minutes=K["SHIFT_CLOSE_GRACE_MINUTES"]):
        return True
    qs = ShiftLock.objects.filter(shift_date=shift_date, shift=shift)
    if site is not None:
        qs = qs.filter(site=site)
    return qs.exists()


def month_key(date):
    return f"{date:%Y-%m}"


def next_month_prep_open(today=None):
    """Next-month planning view opens around day 20 without closing the current month (p.3)."""
    today = today or timezone.localdate()
    return today.day >= K["NEXT_MONTH_PREP_DAY"]
