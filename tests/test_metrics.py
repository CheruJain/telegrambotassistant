"""
Tests for pure calculation logic in app/analytics/metrics.py that doesn't
require hitting the DB (week_bounds is a pure function; the rest are
integration-tested manually against a real Supabase project per README).
"""
import datetime as dt
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.analytics.metrics import week_bounds  # noqa: E402


def test_week_bounds_monday_to_sunday():
    # 2026-09-01 is a Tuesday
    today = dt.date(2026, 9, 1)
    start, end = week_bounds(today)
    assert start == dt.date(2026, 8, 31)  # Monday
    assert end == dt.date(2026, 9, 6)     # Sunday
    assert start.weekday() == 0
    assert end.weekday() == 6
