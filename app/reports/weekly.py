"""Builds the human-readable weekly report text (used both by /summary and
by the automatic Sunday report job)."""
from __future__ import annotations

import datetime as dt

from app.analytics.metrics import weekly_summary_data
from app.ai.analyzer import generate_weekly_insight


def format_weekly_report(user_id: str, today: dt.date) -> str:
    data = weekly_summary_data(user_id, today)
    c = data["content"]
    s = data["sales"]
    period = data["period"]

    lines = ["📊 WEEKLY PERFORMANCE", f"({period['start']} → {period['end']})", ""]

    lines.append("Content:")
    if c["by_platform_account"]:
        for platform, qty in c["by_platform_account"].items():
            lines.append(f"  • {platform}: {qty}")
    else:
        lines.append("  • No content logged this week")
    lines.append(f"  • Total reach: {c['total_reach']}")
    if c["best_performing"]:
        b = c["best_performing"]
        lines.append(
            f"  • Best performing: {b.get('content_type', 'content')} on "
            f"{b.get('platform')} ({b.get('reach', 0)} reach)"
        )

    lines.append("")
    lines.append("Sales:")
    lines.append(f"  • Total calls: {s['total_calls']}")
    lines.append(f"  • Interested: {s['interested']}")
    lines.append(f"  • Follow-ups: {s['follow_ups']}")
    lines.append(f"  • Proposals: {s['proposals']}")
    lines.append(f"  • Won: {s['won']}")
    lines.append(f"  • Conversion rate: {s['conversion_rate_pct']}%")
    lines.append(f"  • Revenue won: ₹{s['revenue_won']:,.0f}")

    if s["by_source"]:
        lines.append("")
        lines.append("Lead Sources:")
        for src, qty in s["by_source"].items():
            lines.append(f"  • {src}: {qty}")

    lines.append("")
    lines.append("🤖 AI INSIGHTS")
    insight = generate_weekly_insight(
        {
            "content": {k: v for k, v in c.items() if k != "content_rows"},
            "sales": {k: v for k, v in s.items() if k != "calls_detailed"},
            "objections": data["objections"],
        }
    )
    lines.append(insight)

    return "\n".join(lines)
