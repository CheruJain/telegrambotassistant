-- ============================================================
-- Telegram AI Assistant - Initial Schema
-- Run this in the Supabase SQL editor (or via psql) once.
-- ============================================================

create extension if not exists "uuid-ossp";

-- ---------------------------------------------------------
-- users
-- ---------------------------------------------------------
create table if not exists users (
    id uuid primary key default uuid_generate_v4(),
    telegram_user_id bigint unique not null,
    name text,
    timezone text not null default 'Asia/Kolkata',
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------
-- user_config  (runtime-editable settings, no code changes needed)
-- ---------------------------------------------------------
create table if not exists user_config (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references users(id) on delete cascade,
    key text not null,
    value text not null,
    updated_at timestamptz not null default now(),
    unique(user_id, key)
);

-- ---------------------------------------------------------
-- daily_activity  (generic work log: content posting counts, work done etc.)
-- ---------------------------------------------------------
create table if not exists daily_activity (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references users(id) on delete cascade,
    date date not null,
    platform text not null,           -- Instagram | LinkedIn | Sales | Other
    account_name text,                -- Instagram Account 1 / 2, LinkedIn, etc.
    activity_type text not null,      -- post, reel, story, call, work_done, etc.
    quantity integer not null default 1,
    notes text,
    created_at timestamptz not null default now()
);

create index if not exists idx_daily_activity_user_date on daily_activity(user_id, date);

-- ---------------------------------------------------------
-- content  (detailed content + analytics)
-- ---------------------------------------------------------
create table if not exists content (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references users(id) on delete cascade,
    platform text not null,           -- Instagram | LinkedIn | Other
    account_name text,
    content_type text not null,       -- Post, Reel, Carousel, Story, Video, Other
    topic text,
    post_date date not null default current_date,
    reach integer,
    likes integer,
    comments integer,
    shares integer,
    saves integer,
    leads_generated integer default 0,
    notes text,
    created_at timestamptz not null default now()
);

create index if not exists idx_content_user_date on content(user_id, post_date);

-- ---------------------------------------------------------
-- sales_calls
-- ---------------------------------------------------------
create table if not exists sales_calls (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references users(id) on delete cascade,
    date date not null default current_date,
    lead_name text,
    source text,                      -- LinkedIn, Instagram Account 1/2, Referral, Other
    call_status text,                 -- Connected, No Answer, Rescheduled ...
    outcome text,                     -- Interested, Not Interested, Follow Up, Proposal, Won, Lost, No Answer, Rescheduled
    objection text,
    follow_up_date date,
    deal_value numeric(12,2),
    notes text,
    created_at timestamptz not null default now()
);

create index if not exists idx_sales_calls_user_date on sales_calls(user_id, date);
create index if not exists idx_sales_calls_followup on sales_calls(user_id, follow_up_date);

-- ---------------------------------------------------------
-- meetings
-- ---------------------------------------------------------
create table if not exists meetings (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references users(id) on delete cascade,
    title text not null,
    person text,
    meeting_type text,                -- sales_call, content_meeting, other
    start_time timestamptz not null,
    end_time timestamptz,
    notes text,
    status text not null default 'scheduled',  -- scheduled, completed, cancelled
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_meetings_user_start on meetings(user_id, start_time);
create index if not exists idx_meetings_status on meetings(status);

-- ---------------------------------------------------------
-- reminders
-- ---------------------------------------------------------
create table if not exists reminders (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references users(id) on delete cascade,
    reminder_text text not null,
    trigger_time timestamptz not null,
    related_meeting_id uuid references meetings(id) on delete cascade,
    status text not null default 'pending',   -- pending, sent, cancelled
    recurrence text,                          -- null | daily | weekly:sun | weekly:mon ...
    created_at timestamptz not null default now(),
    sent_at timestamptz
);

create index if not exists idx_reminders_due on reminders(status, trigger_time);
create index if not exists idx_reminders_meeting on reminders(related_meeting_id);

-- ---------------------------------------------------------
-- processed_messages  (idempotency / duplicate protection)
-- ---------------------------------------------------------
create table if not exists processed_messages (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references users(id) on delete cascade,
    telegram_message_id bigint not null,
    telegram_chat_id bigint not null,
    created_at timestamptz not null default now(),
    unique(telegram_chat_id, telegram_message_id)
);
