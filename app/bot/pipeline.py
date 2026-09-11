"""Sales pipeline lifecycle controls and Telegram inline keyboards."""
from __future__ import annotations

import html
from collections import Counter

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.database import repository as repo
from app.database.client import get_client


STAGE_1 = "stage_1"
FOLLOW_UP_1 = "follow_up_1"
FOLLOW_UP_2 = "follow_up_2"
FOLLOW_UP_3 = "follow_up_3"
STAGE_2 = "stage_2"
STAGE_3 = "stage_3"
STAGE_4 = "stage_4"
DROPPED = "dropped"

ACTIVE_STAGES = {STAGE_1, FOLLOW_UP_1, FOLLOW_UP_2, FOLLOW_UP_3, STAGE_2, STAGE_3, STAGE_4}
DROP_OUTCOMES = {"not interested", "dropped", "exhausted", "not a fit", "backed out", "closed"}


def _safe(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


def _stage(call: dict) -> str:
    value = str(call.get("stage") or call.get("call_status") or "").strip().lower()
    aliases = {
        "normal": STAGE_1,
        "normal_call": STAGE_1,
        "initial": STAGE_1,
        "initial_contact": STAGE_1,
        "call": STAGE_1,
        "followup1": FOLLOW_UP_1,
        "follow-up 1": FOLLOW_UP_1,
        "follow up 1": FOLLOW_UP_1,
        "followup2": FOLLOW_UP_2,
        "follow-up 2": FOLLOW_UP_2,
        "follow up 2": FOLLOW_UP_2,
        "followup3": FOLLOW_UP_3,
        "follow-up 3": FOLLOW_UP_3,
        "follow up 3": FOLLOW_UP_3,
        "discovery": STAGE_2,
        "stage 2": STAGE_2,
        "strategy": STAGE_3,
        "strategy_1hr": STAGE_3,
        "strategy 1hr": STAGE_3,
        "stage 3": STAGE_3,
        "sloshed": STAGE_4,
        "won": STAGE_4,
        "converted": STAGE_4,
        "stage 4": STAGE_4,
    }
    return aliases.get(value, value)


def pipeline_stage_keyboard(call: dict) -> InlineKeyboardMarkup:
    """Return the mandatory lifecycle actions for the lead's current stage."""
    call_id = str(call["id"])
    stage = _stage(call)

    if stage == STAGE_1:
        rows = [[
            InlineKeyboardButton("🎯 Discovery", callback_data=f"pipeline:discovery:{call_id}"),
            InlineKeyboardButton("📵 No Pick (➔ F1)", callback_data=f"pipeline:f1:{call_id}"),
            InlineKeyboardButton("❌ Not Interested", callback_data=f"pipeline:drop:{call_id}"),
        ]]
    elif stage == FOLLOW_UP_1:
        rows = [[
            InlineKeyboardButton("🎯 Connected ➔ Discovery", callback_data=f"pipeline:discovery:{call_id}"),
            InlineKeyboardButton("📵 No Pick (Next F)", callback_data=f"pipeline:f2:{call_id}"),
            InlineKeyboardButton("❌ Not Interested", callback_data=f"pipeline:drop:{call_id}"),
        ]]
    elif stage == FOLLOW_UP_2:
        rows = [[
            InlineKeyboardButton("🎯 Connected ➔ Discovery", callback_data=f"pipeline:discovery:{call_id}"),
            InlineKeyboardButton("📵 No Pick (Next F)", callback_data=f"pipeline:f3:{call_id}"),
            InlineKeyboardButton("❌ Not Interested", callback_data=f"pipeline:drop:{call_id}"),
        ]]
    elif stage == FOLLOW_UP_3:
        rows = [[
            InlineKeyboardButton("🎯 Connected ➔ Discovery", callback_data=f"pipeline:discovery:{call_id}"),
            InlineKeyboardButton("❌ No Pick (Auto Drop)", callback_data=f"pipeline:exhaust:{call_id}"),
            InlineKeyboardButton("❌ Not Interested", callback_data=f"pipeline:drop:{call_id}"),
        ]]
    elif stage == STAGE_2:
        rows = [[
            InlineKeyboardButton("🧠 Move to Strategy 1hr", callback_data=f"pipeline:strategy:{call_id}"),
            InlineKeyboardButton("❌ Drop Lead", callback_data=f"pipeline:drop:{call_id}"),
        ]]
    elif stage == STAGE_3:
        rows = [[
            InlineKeyboardButton("🏆 Sloshed (Deal Won)", callback_data=f"pipeline:won:{call_id}"),
            InlineKeyboardButton("❌ Drop Lead", callback_data=f"pipeline:drop:{call_id}"),
        ]]
    else:
        rows = []
    return InlineKeyboardMarkup(rows)


def stage_1_keyboard(call: dict) -> InlineKeyboardMarkup:
    return pipeline_stage_keyboard({**call, "call_status": STAGE_1})


def follow_up_keyboard(call: dict, follow_up_number: int) -> InlineKeyboardMarkup:
    stage = {1: FOLLOW_UP_1, 2: FOLLOW_UP_2, 3: FOLLOW_UP_3}[follow_up_number]
    return pipeline_stage_keyboard({**call, "call_status": stage})


def stage_2_keyboard(call: dict) -> InlineKeyboardMarkup:
    return pipeline_stage_keyboard({**call, "call_status": STAGE_2})


def stage_3_keyboard(call: dict) -> InlineKeyboardMarkup:
    return pipeline_stage_keyboard({**call, "call_status": STAGE_3})


def _update_call(call_id: str, *, stage: str, outcome: str, notes: str | None = None) -> dict:
    """Persist lifecycle state using the existing sales_calls schema."""
    payload = {"call_status": stage, "outcome": outcome}
    if notes:
        payload["notes"] = notes
    result = get_client().table("sales_calls").update(payload).eq("id", call_id).execute()
    if not result.data:
        raise ValueError("Sales call not found")
    return result.data[0]


def _get_call(user_id: str, call_id: str) -> dict | None:
    result = get_client().table("sales_calls").select("*").eq("id", call_id).eq("user_id", user_id).limit(1).execute()
    return result.data[0] if result.data else None


def _advance(call: dict, action: str) -> tuple[str, str, str]:
    stage = _stage(call)
    if action == "discovery":
        if stage not in {STAGE_1, FOLLOW_UP_1, FOLLOW_UP_2, FOLLOW_UP_3}:
            raise ValueError("Discovery transition is not valid for this stage")
        return STAGE_2, "Interested", "Moved to Discovery"
    if action == "f1" and stage == STAGE_1:
        return FOLLOW_UP_1, "Follow Up", "No pick — Follow-up 1"
    if action == "f2" and stage == FOLLOW_UP_1:
        return FOLLOW_UP_2, "Follow Up", "No pick — Follow-up 2"
    if action == "f3" and stage == FOLLOW_UP_2:
        return FOLLOW_UP_3, "Follow Up", "No pick — Follow-up 3"
    if action == "exhaust" and stage == FOLLOW_UP_3:
        return DROPPED, "Exhausted", "Auto dropped after three no-pick follow-ups"
    if action == "strategy" and stage == STAGE_2:
        return STAGE_3, "Qualified", "Qualified — moved to Strategy 1hr"
    if action == "won" and stage == STAGE_3:
        return STAGE_4, "Converted", "Deal won — converted"
    if action == "drop" and stage in ACTIVE_STAGES:
        return DROPPED, "Not Interested", "Lead dropped"
    raise ValueError("Invalid pipeline transition")


async def pipeline_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    data = query.data or ""
    if not data.startswith("pipeline:"):
        return False

    await query.answer()
    user = repo.get_or_create_user(update.effective_user.id, update.effective_user.first_name)
    try:
        _, action, call_id = data.split(":", 2)
        call = _get_call(user["id"], call_id)
        if not call:
            await query.message.reply_text("Lead not found or no longer belongs to this account.")
            return True
        stage, outcome, note = _advance(call, action)
        updated = _update_call(call_id, stage=stage, outcome=outcome, notes=note)
        name = _safe(updated.get("lead_name") or "Lead")

        if stage == DROPPED:
            await query.edit_message_text(
                f"<b>🚫 LEAD DROPPED</b>\n━━━━━━━━━━━━━━━━━━━━\n• <b>{name}</b>\n• {_safe(outcome)}\n━━━━━━━━━━━━━━━━━━━━",
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                f"<b>PIPELINE UPDATED</b>\n━━━━━━━━━━━━━━━━━━━━\n• <b>{name}</b>\n• {_safe(stage.replace('_', ' ').title())}\n• {_safe(outcome)}\n━━━━━━━━━━━━━━━━━━━━",
                parse_mode="HTML",
                reply_markup=pipeline_stage_keyboard(updated),
            )
    except ValueError as exc:
        await query.message.reply_text(str(exc))
    except Exception:
        await query.message.reply_text("Unable to update this lead right now.")
    return True


def pipeline_overview(user_id: str) -> tuple[str, list[dict]]:
    calls = repo.get_sales_calls(user_id, "2000-01-01", "2100-12-31")
    active = []
    counts = Counter()
    dropped = 0
    for call in calls:
        stage = _stage(call)
        outcome = str(call.get("outcome") or "").strip().lower()
        if stage == DROPPED or outcome in DROP_OUTCOMES:
            dropped += 1
            continue
        if stage in ACTIVE_STAGES:
            counts[stage] += 1
            active.append(call)

    text = "\n".join([
        "📊  SALES PIPELINE OVERVIEW",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📞  Stage 1 (Normal Call): {counts[STAGE_1]}",
        f"🔍  Stage 2 (Discovery): {counts[STAGE_2]}",
        f"🧠  Stage 3 (Strategy 1hr): {counts[STAGE_3]}",
        f"🎉  Stage 4 (Sloshed): {counts[STAGE_4]}",
        "",
        "📵  NO-PICK QUEUE",
        f"• Follow-up 1: {counts[FOLLOW_UP_1]}",
        f"• Follow-up 2: {counts[FOLLOW_UP_2]}",
        f"• Follow-up 3: {counts[FOLLOW_UP_3]}",
        "",
        f"🚫  DROPPED / LOST: {dropped}",
        "━━━━━━━━━━━━━━━━━━━━",
    ])
    return text, sorted(active, key=lambda row: (str(row.get("lead_name") or "").lower(), str(row.get("created_at") or "")))


async def pipeline_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = repo.get_or_create_user(update.effective_user.id, update.effective_user.first_name)
    overview, active = pipeline_overview(user["id"])
    await update.message.reply_text(overview)
    for call in active:
        stage = _stage(call)
        labels = {
            STAGE_1: "Stage 1 • Normal Call",
            FOLLOW_UP_1: "Follow-up 1",
            FOLLOW_UP_2: "Follow-up 2",
            FOLLOW_UP_3: "Follow-up 3",
            STAGE_2: "Stage 2 • Discovery",
            STAGE_3: "Stage 3 • Strategy 1hr",
            STAGE_4: "Stage 4 • Sloshed",
        }
        phone = str(call.get("phone_number") or "").strip()
        card = f"<b>{_safe(call.get('lead_name') or 'Unknown Lead')}</b>\n{_safe(labels.get(stage, stage))}"
        if phone:
            card += f"\n📞 <code>{_safe(phone)}</code>"
        await update.message.reply_text(card, parse_mode="HTML", reply_markup=pipeline_stage_keyboard(call))
