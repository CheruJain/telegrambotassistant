"""
Unit tests for the deterministic date/time resolver (app/ai/parser.py).
These do NOT hit the network/AI/DB - pure function tests.

Run with: pytest tests/
"""
import datetime as dt
import pytz
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.parser import resolve_datetime  # noqa: E402

TZ = pytz.timezone("Asia/Kolkata")
NOW = TZ.localize(dt.datetime(2026, 9, 1, 10, 0))  # a Tuesday


def test_kal_4_baje():
    result = resolve_datetime("kal 4 baje", "Asia/Kolkata", now=NOW)
    assert result.date() == (NOW.date() + dt.timedelta(days=1))
    assert result.hour == 16  # "4 baje" with no am/pm, hour<=7 -> PM


def test_aaj():
    result = resolve_datetime("aaj 9 baje", "Asia/Kolkata", now=NOW)
    assert result.date() == NOW.date()


def test_minutes_from_now():
    result = resolve_datetime("30 minutes mein", "Asia/Kolkata", now=NOW)
    assert (result - NOW).seconds == 30 * 60


def test_explicit_am_pm():
    result = resolve_datetime("11 AM", "Asia/Kolkata", now=NOW)
    assert result.hour == 11


def test_friday():
    result = resolve_datetime("Friday 11 AM", "Asia/Kolkata", now=NOW)
    assert result.strftime("%A") == "Friday"
    assert result.hour == 11


def test_empty_returns_none():
    assert resolve_datetime(None, "Asia/Kolkata", now=NOW) is None
    assert resolve_datetime("   ", "Asia/Kolkata", now=NOW) is None
