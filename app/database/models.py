"""
Lightweight pydantic models describing the shape of each table.

These are NOT used as an ORM (we talk to Supabase via the repository module
using plain dicts, since PostgREST already gives us JSON back). They exist so
the rest of the codebase - and anyone reading it - has a single source of
truth for what fields exist on each entity, and so the AI parser can be told
"fill in this schema" with confidence it matches the DB.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional
from pydantic import BaseModel


class User(BaseModel):
    id: Optional[str] = None
    telegram_user_id: int
    name: Optional[str] = None
    timezone: str = "Asia/Kolkata"


class DailyActivity(BaseModel):
    id: Optional[str] = None
    user_id: str
    date: dt.date
    platform: str  # Instagram | LinkedIn | Sales | Other
    account_name: Optional[str] = None
    activity_type: str
    quantity: int = 1
    notes: Optional[str] = None


class Content(BaseModel):
    id: Optional[str] = None
    user_id: str
    platform: str
    account_name: Optional[str] = None
    content_type: str  # Post | Reel | Carousel | Story | Video | Other
    topic: Optional[str] = None
    post_date: dt.date
    reach: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    saves: Optional[int] = None
    leads_generated: int = 0
    notes: Optional[str] = None


class SalesCall(BaseModel):
    id: Optional[str] = None
    user_id: str
    date: dt.date
    lead_name: Optional[str] = None
    source: Optional[str] = None
    call_status: Optional[str] = None
    outcome: Optional[str] = None
    objection: Optional[str] = None
    follow_up_date: Optional[dt.date] = None
    deal_value: Optional[float] = None
    notes: Optional[str] = None


class Meeting(BaseModel):
    id: Optional[str] = None
    user_id: str
    title: str
    person: Optional[str] = None
    meeting_type: Optional[str] = None
    start_time: dt.datetime
    end_time: Optional[dt.datetime] = None
    notes: Optional[str] = None
    status: str = "scheduled"  # scheduled | completed | cancelled


class Reminder(BaseModel):
    id: Optional[str] = None
    user_id: str
    reminder_text: str
    trigger_time: dt.datetime
    related_meeting_id: Optional[str] = None
    status: str = "pending"  # pending | sent | cancelled
    recurrence: Optional[str] = None  # None | daily | weekly:mon..sun
