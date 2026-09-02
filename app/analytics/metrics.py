"""
Deterministic statistics. No AI is used here on purpose (spec section 28) -
counting and summing rows is something plain Python does perfectly and for
free.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from app.database import repository as repo


def daterange_str(start: dt.date, end: dt.date) -> tuple[str, str]:
    return start.isoformat(), end.isoformat()


def week_bounds(today: dt.date) -> tuple[dt.date, dt.date]:
    """Monday..Sunday week containing `today`."""
    start = today - dt.timedelta(days=today.weekday())
    end = start + dt.timedelta(days=6)
    return start, end


def content_summary(user_id: str, start: dt.date, end: dt.date) -> dict:
    s, e = daterange_str(start, end)
    activity = repo.get_activity(user_id, s, e)
    content = repo.get_content(user_id, s, e)

    by_platform_account = defaultdict(int)
    for row in activity:
        if row["platform"] in ("Instagram", "LinkedIn"):
            key = row.get("account_name") or row["platform"]
            by_platform_account[key] += row["quantity"]

    total_reach = sum((c.get("reach") or 0) for c in content)
    total_leads = sum((c.get("leads_generated") or 0) for c in content)

    best = None
    if content:
        scored = [c for c in content if c.get("reach") is not None]
        if scored:
            best = max(scored, key=lambda c: c.get("reach") or 0)

    return {
        "by_platform_account": dict(by_platform_account),
        "total_reach": total_reach,
        "total_leads_from_content": total_leads,
        "best_performing": best,
        "content_rows": content,
    }


def sales_summary(user_id: str, start: dt.date, end: dt.date) -> dict:
    s, e = daterange_str(start, end)
    calls_detailed = repo.get_sales_calls(user_id, s, e)
    activity = repo.get_activity(user_id, s, e)

    def bulk(activity_type: str) -> int:
        return sum(
            row["quantity"]
            for row in activity
            if row["platform"] == "Sales" and row["activity_type"] == activity_type
        )

    bulk_calls = bulk("calls")
    bulk_interested = bulk("interested")
    bulk_follow_ups = bulk("follow_ups")
    bulk_wins = bulk("wins")

    total_calls = bulk_calls + len(calls_detailed)
    interested = bulk_interested + sum(1 for c in calls_detailed if c.get("outcome") == "Interested")
    follow_ups = bulk_follow_ups + sum(1 for c in calls_detailed if c.get("outcome") == "Follow Up")
    proposals = sum(1 for c in calls_detailed if c.get("outcome") == "Proposal")
    won = [c for c in calls_detailed if c.get("outcome") == "Won"]
    won_count = bulk_wins + len(won)
    lost = sum(1 for c in calls_detailed if c.get("outcome") == "Lost")
    revenue = sum((c.get("deal_value") or 0) for c in won)
    potential_value = sum((c.get("deal_value") or 0) for c in calls_detailed if c.get("outcome") not in ("Lost",))

    conversion_rate = round((won_count / total_calls) * 100, 1) if total_calls else 0.0

    by_source = defaultdict(int)
    for c in calls_detailed:
        if c.get("source"):
            by_source[c["source"]] += 1

    return {
        "total_calls": total_calls,
        "interested": interested,
        "follow_ups": follow_ups,
        "proposals": proposals,
        "won": won_count,
        "lost": lost,
        "conversion_rate_pct": conversion_rate,
        "revenue_won": revenue,
        "potential_deal_value": potential_value,
        "by_source": dict(by_source),
        "calls_detailed": calls_detailed,
    }


def objections_list(user_id: str, start: dt.date, end: dt.date) -> list[str]:
    s, e = daterange_str(start, end)
    calls = repo.get_sales_calls(user_id, s, e)
    return [c["objection"] for c in calls if c.get("objection")]


def daily_summary_data(user_id: str, today: dt.date) -> dict:
    content = content_summary(user_id, today, today)
    sales = sales_summary(user_id, today, today)

    now_iso = dt.datetime.utcnow().isoformat()
    end_of_day_iso = dt.datetime.combine(today, dt.time(23, 59, 59)).isoformat()
    meetings = repo.get_meetings_in_range(user_id, now_iso, end_of_day_iso)
    followups = repo.get_pending_followups(user_id)
    reminders = repo.get_pending_reminders(user_id)

    return {
        "content": content,
        "sales": sales,
        "meetings": meetings,
        "pending_followups": followups,
        "pending_reminders": reminders,
    }


def weekly_summary_data(user_id: str, today: dt.date) -> dict:
    start, end = week_bounds(today)
    content = content_summary(user_id, start, end)
    sales = sales_summary(user_id, start, end)
    objections = objections_list(user_id, start, end)
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "content": content,
        "sales": sales,
        "objections": objections,
    }
