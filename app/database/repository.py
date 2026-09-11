"""Supabase/Postgres data access helpers."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from app.config.settings import settings
from app.database.client import get_client


def get_or_create_user(telegram_user_id: int, name: Optional[str] = None) -> dict:
    db = get_client()
    res = db.table("users").select("*").eq("telegram_user_id", telegram_user_id).execute()
    if res.data: return res.data[0]
    res = db.table("users").insert({"telegram_user_id": telegram_user_id, "name": name or settings.USER_NAME, "timezone": settings.DEFAULT_TIMEZONE}).execute()
    return res.data[0]

def is_authorized(telegram_user_id: int) -> bool: return telegram_user_id == settings.TELEGRAM_USER_ID

def get_config(user_id: str, key: str, default: str | None = None) -> str | None:
    res = get_client().table("user_config").select("value").eq("user_id", user_id).eq("key", key).execute(); return res.data[0]["value"] if res.data else default

def set_config(user_id: str, key: str, value: str) -> None: get_client().table("user_config").upsert({"user_id": user_id, "key": key, "value": str(value)}, on_conflict="user_id,key").execute()
def already_processed(user_id: str, chat_id: int, message_id: int) -> bool: return bool(get_client().table("processed_messages").select("id").eq("telegram_chat_id", chat_id).eq("telegram_message_id", message_id).execute().data)
def mark_processed(user_id: str, chat_id: int, message_id: int) -> None:
    try: get_client().table("processed_messages").insert({"user_id": user_id, "telegram_chat_id": chat_id, "telegram_message_id": message_id}).execute()
    except Exception: pass

def log_activity(user_id: str, date: str, platform: str, activity_type: str, quantity: int = 1, account_name: str | None = None, notes: str | None = None) -> dict:
    res = get_client().table("daily_activity").insert({"user_id": user_id, "date": date, "platform": platform, "account_name": account_name, "activity_type": activity_type, "quantity": quantity, "notes": notes}).execute(); return res.data[0]
def get_activity(user_id: str, start_date: str, end_date: str, created_after: str | None = None) -> list[dict]:
    q = get_client().table("daily_activity").select("*").eq("user_id", user_id).gte("date", start_date).lte("date", end_date)
    if created_after: q = q.gt("created_at", created_after)
    return q.order("created_at").execute().data

def log_content(user_id: str, platform: str, content_type: str, **kwargs) -> dict:
    payload = {"user_id": user_id, "platform": platform, "content_type": content_type, "post_date": kwargs.get("post_date") or dt.date.today().isoformat(), **{k: v for k, v in kwargs.items() if k != "post_date"}}
    res = get_client().table("content").insert(payload).execute(); return res.data[0]
def get_content(user_id: str, start_date: str, end_date: str, created_after: str | None = None) -> list[dict]:
    q = get_client().table("content").select("*").eq("user_id", user_id).gte("post_date", start_date).lte("post_date", end_date)
    if created_after: q = q.gt("created_at", created_after)
    return q.order("created_at").execute().data
def update_content_metrics(content_id: str, **metrics) -> dict:
    res = get_client().table("content").update(metrics).eq("id", content_id).execute(); return res.data[0] if res.data else {}
def find_recent_content(user_id: str, platform: str | None = None, limit: int = 5) -> list[dict]:
    q = get_client().table("content").select("*").eq("user_id", user_id)
    if platform: q = q.eq("platform", platform)
    return q.order("created_at", desc=True).limit(limit).execute().data

def log_social_metric(user_id: str, platform: str, account_name: str, metric_date: str, followers: int | None = None, reach: int | None = None, views: int | None = None, leads: int | None = None, notes: str | None = None) -> dict:
    payload = {"user_id": user_id, "platform": platform, "account_name": account_name, "metric_date": metric_date, "followers": followers, "reach": reach, "views": views, "leads": leads, "notes": notes}
    res = get_client().table("social_metrics").insert(payload).execute(); return res.data[0]

def log_sales_calls_bulk(user_id: str, date: str, total_calls: int, notes: str | None = None) -> dict: return log_activity(user_id, date, "Sales", "calls", quantity=total_calls, notes=notes)
def log_sales_call(user_id: str, date: str, lead_name: str | None = None, source: str | None = None, call_status: str | None = None, outcome: str | None = None, objection: str | None = None, follow_up_date: str | None = None, deal_value: float | None = None, notes: str | None = None, phone_number: str | None = None, booked_date: str | None = None, booked_time: str | None = None, call_type: str | None = None, confirmation_reminder_sent: bool = False) -> dict:
    payload = {"user_id": user_id, "date": date, "lead_name": lead_name, "source": source, "call_status": call_status, "outcome": outcome, "objection": objection, "follow_up_date": follow_up_date, "deal_value": deal_value, "notes": notes, "phone_number": phone_number, "booked_date": booked_date, "booked_time": booked_time, "call_type": call_type, "confirmation_reminder_sent": confirmation_reminder_sent}; res = get_client().table("sales_calls").insert(payload).execute(); return res.data[0]
def get_sales_calls(user_id: str, start_date: str, end_date: str, created_after: str | None = None) -> list[dict]:
    q = get_client().table("sales_calls").select("*").eq("user_id", user_id).gte("date", start_date).lte("date", end_date)
    if created_after: q = q.gt("created_at", created_after)
    return q.order("created_at").execute().data
def get_booked_sales_calls_for_date(user_id: str, booked_date: str) -> list[dict]: return get_client().table("sales_calls").select("*").eq("user_id", user_id).eq("booked_date", booked_date).eq("outcome", "Booked").order("booked_time").execute().data
def get_pending_followups(user_id: str, as_of_date: str | None = None) -> list[dict]:
    q=get_client().table("sales_calls").select("*").eq("user_id",user_id).not_.is_("follow_up_date","null").in_("outcome",["Follow Up","Interested","Proposal","Rescheduled"])
    if as_of_date:q=q.lte("follow_up_date",as_of_date)
    return q.order("follow_up_date").execute().data

def create_meeting(user_id: str, title: str, start_time_iso: str, person: str | None = None, meeting_type: str | None = None, end_time_iso: str | None = None, notes: str | None = None) -> dict:
    res=get_client().table("meetings").insert({"user_id":user_id,"title":title,"person":person,"meeting_type":meeting_type,"start_time":start_time_iso,"end_time":end_time_iso,"notes":notes,"status":"scheduled"}).execute(); return res.data[0]
def _utc_now_iso() -> str: return dt.datetime.now(dt.timezone.utc).isoformat()
def find_meeting_by_person_or_title(user_id: str, search_text: str, only_upcoming: bool = True) -> list[dict]:
    q=get_client().table("meetings").select("*").eq("user_id",user_id).neq("status","cancelled")
    if only_upcoming:q=q.gte("start_time",_utc_now_iso())
    res=q.order("start_time").execute(); text=search_text.lower().strip(); return [m for m in res.data if text in (m.get("person") or "").lower() or text in (m.get("title") or "").lower()]
def update_meeting(meeting_id: str, **fields) -> dict:
    fields["updated_at"]=_utc_now_iso(); res=get_client().table("meetings").update(fields).eq("id",meeting_id).execute(); return res.data[0] if res.data else {}
def cancel_meeting(meeting_id: str) -> dict: return update_meeting(meeting_id,status="cancelled")
def get_meeting(meeting_id: str) -> dict | None:
    res=get_client().table("meetings").select("*").eq("id",meeting_id).execute(); return res.data[0] if res.data else None
def get_meetings_in_range(user_id: str, start_iso: str, end_iso: str, created_after: str | None = None) -> list[dict]:
    q=get_client().table("meetings").select("*").eq("user_id",user_id).neq("status","cancelled").gte("start_time",start_iso).lte("start_time",end_iso)
    if created_after:q=q.gt("created_at",created_after)
    return q.order("start_time").execute().data
def get_next_meeting(user_id: str) -> dict | None:
    res=get_client().table("meetings").select("*").eq("user_id",user_id).neq("status","cancelled").gte("start_time",_utc_now_iso()).order("start_time").limit(1).execute(); return res.data[0] if res.data else None

def create_reminder(user_id: str, reminder_text: str, trigger_time_iso: str, related_meeting_id: str | None = None, recurrence: str | None = None) -> dict:
    res=get_client().table("reminders").insert({"user_id":user_id,"reminder_text":reminder_text,"trigger_time":trigger_time_iso,"related_meeting_id":related_meeting_id,"recurrence":recurrence,"status":"pending"}).execute(); return res.data[0]
def cancel_reminders_for_meeting(meeting_id: str) -> None: get_client().table("reminders").update({"status":"cancelled"}).eq("related_meeting_id",meeting_id).eq("status","pending").execute()
def get_due_reminders(as_of_iso: str) -> list[dict]: return get_client().table("reminders").select("*").eq("status","pending").lte("trigger_time",as_of_iso).execute().data
def mark_reminder_sent(reminder_id: str) -> None: get_client().table("reminders").update({"status":"sent","sent_at":_utc_now_iso()}).eq("id",reminder_id).execute()
def cancel_reminder(reminder_id: str) -> None: get_client().table("reminders").update({"status":"cancelled"}).eq("id",reminder_id).execute()
def update_reminder(reminder_id: str, **fields) -> dict:
    res=get_client().table("reminders").update(fields).eq("id",reminder_id).execute(); return res.data[0] if res.data else {}
def find_pending_reminder(user_id: str, search_text: str) -> list[dict]:
    res=get_client().table("reminders").select("*").eq("user_id",user_id).eq("status","pending").order("trigger_time").execute(); text=search_text.lower().strip(); return [r for r in res.data if text in (r.get("reminder_text") or "").lower()]

def ensure_booking_confirmation_reminders(user_id: str, now: dt.datetime | None = None) -> int:
    db=get_client(); ist=dt.timezone(dt.timedelta(hours=5,minutes=30)); now=(now or dt.datetime.now(ist)).astimezone(ist); rows=db.table("sales_calls").select("*").eq("user_id",user_id).eq("outcome","Booked").execute().data or []; created_count=0
    for call in rows:
        if not call.get("lead_name") or not call.get("booked_date") or not call.get("booked_time"):continue
        try:call_start=dt.datetime.combine(dt.date.fromisoformat(str(call["booked_date"])),dt.time.fromisoformat(str(call["booked_time"])[:8]),tzinfo=ist)
        except ValueError:continue
        if call_start<=now:continue
        reminder_time=call_start-dt.timedelta(minutes=60)
        if reminder_time<=now:continue
        meetings=db.table("meetings").select("id,start_time,person,status").eq("user_id",user_id).eq("status","scheduled").execute().data or []; meeting_id=None
        for meeting in meetings:
            if (meeting.get("person") or "").strip().lower()!=str(call["lead_name"]).strip().lower():continue
            try:meeting_start=dt.datetime.fromisoformat(str(meeting["start_time"]).replace("Z","+00:00")); meeting_start=(meeting_start if meeting_start.tzinfo else meeting_start.replace(tzinfo=dt.timezone.utc)).astimezone(ist)
            except ValueError:continue
            if meeting_start==call_start:meeting_id=meeting["id"];break
        pending=db.table("reminders").select("id,trigger_time,related_meeting_id,reminder_text").eq("user_id",user_id).eq("status","pending").execute().data or []; exists=False
        for reminder in pending:
            if meeting_id and reminder.get("related_meeting_id")==meeting_id:exists=True;break
            try:trigger=dt.datetime.fromisoformat(str(reminder["trigger_time"]).replace("Z","+00:00"));trigger=(trigger if trigger.tzinfo else trigger.replace(tzinfo=dt.timezone.utc)).astimezone(ist)
            except ValueError:continue
            if abs((trigger-reminder_time).total_seconds())<=60 and str(call["lead_name"]).lower() in str(reminder.get("reminder_text") or "").lower():exists=True;break
        if exists:continue
        reminder_text=f"Confirm attendance: {call['lead_name']} — sales call at {call_start.strftime('%I:%M %p').lstrip('0')}"; reminder_text+=f"\nPhone: {call['phone_number']}" if call.get("phone_number") else ""; create_reminder(user_id,reminder_text,reminder_time.isoformat(),related_meeting_id=meeting_id);created_count+=1
    return created_count

def get_pending_reminders(user_id: str) -> list[dict]: return get_client().table("reminders").select("*").eq("user_id",user_id).eq("status","pending").order("trigger_time").execute().data
def reschedule_recurring(reminder: dict,next_trigger_iso: str) -> dict:
    payload={"user_id":reminder["user_id"],"reminder_text":reminder["reminder_text"],"trigger_time":next_trigger_iso,"related_meeting_id":reminder.get("related_meeting_id"),"recurrence":reminder.get("recurrence"),"status":"pending"};res=get_client().table("reminders").insert(payload).execute();return res.data[0]
def find_booked_sales_call(user_id: str, lead_name: str, booked_date: str | None = None, phone_number: str | None = None) -> dict | None:
    q=get_client().table("sales_calls").select("*").eq("user_id",user_id).eq("outcome","Booked");q=q.ilike("lead_name",lead_name) if lead_name else q
    if booked_date:q=q.eq("booked_date",booked_date)
    if phone_number:q=q.eq("phone_number",phone_number)
    rows=q.order("booked_date").order("booked_time").execute().data;return rows[0] if len(rows)==1 else None
def cancel_booked_sales_call_and_reminders(user_id: str, lead_name: str, booked_date: str | None = None, phone_number: str | None = None) -> int:
    db=get_client();q=db.table("sales_calls").select("id,lead_name,booked_date,booked_time,phone_number").eq("user_id",user_id).eq("lead_name",lead_name).eq("outcome","Booked")
    if booked_date:q=q.eq("booked_date",booked_date)
    if phone_number:q=q.eq("phone_number",phone_number)
    rows=q.execute().data
    if not rows:return 0
    ids=[r["id"] for r in rows];db.table("sales_calls").update({"outcome":"Cancelled"}).in_("id",ids).execute();meetings=db.table("meetings").select("id,person,start_time").eq("user_id",user_id).eq("status","scheduled").execute().data
    for call in rows:
        for meeting in meetings:
            if (meeting.get("person") or "").strip().lower()!=(call.get("lead_name") or "").strip().lower():continue
            start=str(meeting.get("start_time") or "")
            if call.get("booked_date") and call["booked_date"] not in start:continue
            if call.get("booked_time") and str(call["booked_time"])[:5] not in start:continue
            cancel_reminders_for_meeting(meeting["id"])
    return len(ids)
def get_assigned_tasks_for_date(user_id: str, assigned_date: str, created_after: str | None = None) -> list[dict]:
    q=get_client().table("assigned_tasks").select("*").eq("user_id",user_id).eq("assigned_date",assigned_date).order("created_at")
    if created_after:q=q.gt("created_at",created_after)
    return q.execute().data
def get_daily_digest_data(user_id: str, target_date: str, created_after: str | None = None) -> dict:
    return {"work":get_activity(user_id,target_date,target_date,created_after=created_after),"content":get_content(user_id,target_date,target_date,created_after=created_after),"sales":get_sales_calls(user_id,target_date,target_date,created_after=created_after),"meetings":get_meetings_in_range(user_id,f"{target_date}T00:00:00",f"{target_date}T23:59:59",created_after=created_after),"tasks":get_assigned_tasks_for_date(user_id,target_date,created_after=created_after)}
