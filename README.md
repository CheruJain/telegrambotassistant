# Telegram AI Work / Sales / Productivity Assistant

A single-user personal assistant that lives inside Telegram. Talk to it in
English, Hindi, or Hinglish and it will log your content posting, sales
calls, meetings, and reminders — no Google Calendar, Notion, or Google
Sheets required anywhere in the core system.

```
TELEGRAM → PYTHON BACKEND → AI LAYER → SUPABASE (Postgres) → INTERNAL SCHEDULER → TELEGRAM ALERTS
```

## 1. What's included (Version 1)

- Natural-language logging of work, content (Instagram x2 + LinkedIn), and sales calls
- Internal meetings system (create / update / cancel via chat)
- Internal reminder scheduler (polls its own DB, sends Telegram messages — replaces Google Calendar notifications)
- Recurring reminders (daily / weekly)
- `/today` and `/summary` (daily + weekly reports, with AI insights on the weekly one)
- Automatic weekly report sent every Sunday (configurable)
- Pending follow-ups tracking
- Sales objection pattern Q&A
- Duplicate-message protection, single-user auth, honest error handling
- Deterministic date parsing (no wasted AI calls) + a single AI call per free-text message

Voice-message transcription and Google-Calendar-free future integrations are
stubbed / documented but intentionally **not** required for v1 (see spec
sections 21, 22, 32 in the original brief).

## 2. Project structure

```
telegram-ai-assistant/
├── app/
│   ├── bot/            # Telegram handlers + slash commands
│   ├── ai/             # prompts, NL parser, weekly-insight analyzer
│   ├── database/        # Supabase client, repository (all DB access), models
│   ├── reminders/       # APScheduler-based internal scheduler
│   ├── analytics/       # pure-python stats (no AI)
│   ├── reports/         # weekly report formatting
│   └── config/          # settings.py (env-var loader)
├── tests/               # pytest unit tests (date parsing, week bounds)
├── main.py               # entrypoint
├── requirements.txt
├── .env.example
└── README.md (this file)
```

## 3. Prerequisites

- Python 3.11+
- A free [Supabase](https://supabase.com) project (Postgres + REST API)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- An [Anthropic API key](https://console.anthropic.com) (used for the NL parser and weekly insights)
- Your own numeric Telegram user ID (get it from [@userinfobot](https://t.me/userinfobot))

## 4. Telegram bot setup

1. Message **@BotFather** on Telegram → `/newbot` → follow the prompts.
2. Copy the token it gives you into `TELEGRAM_BOT_TOKEN` in `.env`.
3. Message **@userinfobot** to get your numeric user ID → put it in `TELEGRAM_USER_ID`.
   This is the *only* Telegram account the bot will respond to.

## 5. Supabase setup

1. Create a new project at supabase.com (free tier is fine).
2. In the Supabase dashboard, go to **SQL Editor** → paste the contents of
   `app/database/migrations/001_init.sql` → run it. This creates every table
   (`users`, `daily_activity`, `content`, `sales_calls`, `meetings`,
   `reminders`, `user_config`, `processed_messages`).
3. Go to **Project Settings → API** → copy the **Project URL** into
   `SUPABASE_URL` and the **service_role key** (or anon key with RLS
   disabled, for a single-user personal bot) into `SUPABASE_KEY`.

> This bot is designed for one private user, so Row Level Security can stay
> off, or you can add a policy scoped to the single `users.telegram_user_id`
> row if you prefer defense-in-depth.

## 6. AI API setup

1. Get an API key from console.anthropic.com.
2. Put it in `AI_API_KEY`.
3. `AI_MODEL` defaults to `claude-sonnet-4-6` — change it in `.env` if you want a different model.

## 7. Install & configure

```bash
git clone <this-repo>
cd telegram-ai-assistant
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# now edit .env and fill in all 5 required secrets
```

## 8. Run locally

```bash
python main.py
```

You should see `Assistant ready for user <your_id>` in the logs. Open
Telegram, message your bot `/start`, and try:

```
Aaj 40 sales calls hui, 7 interested aur 3 follow-up.
Kal 4 baje Rahul ke saath sales call hai.
LinkedIn pe ek post kari.
Aaj ka summary de.
```

## 9. Configuration (no code changes needed)

Runtime-tunable settings live as env-var defaults in `.env` and can also be
overridden per-user in the `user_config` table (key/value) via
`app/database/repository.py:get_config/set_config` — wire a `/config`
command to it if you want to change things from Telegram itself. Configurable:

- `DEFAULT_TIMEZONE` (default `Asia/Kolkata`)
- `WEEKLY_REPORT_DAY`, `WEEKLY_REPORT_HOUR`, `WEEKLY_REPORT_MINUTE`
- `DEFAULT_MEETING_REMINDER_MINUTES` (default 30)
- `SCHEDULER_POLL_SECONDS` (default 30)
- `USER_NAME`

## 10. Testing

```bash
pytest tests/ -q
```

Unit tests cover the deterministic date/time resolver (`kal`, `Friday 11 AM`,
`30 minutes mein`, etc.) and week-boundary math — the parts that must never
depend on network access. End-to-end behavior (the 11 scenarios in the
original spec, e.g. "log a sales call", "reschedule a meeting", "Hinglish
meeting + custom reminder") should be run manually against a real bot once
deployed, since they require live Telegram + Supabase + AI calls:

1. `Aaj 40 sales calls hui, 7 interested aur 3 follow-up.` → check `/today`
2. `Aaj IG 1 pe 2 reels aur IG 2 pe 1 carousel dali.` → check `/today`
3. `Kal 4 PM Rahul ke saath sales call hai.` → meeting + reminder created
4. `Kal 10 AM Amit ko call karne ka reminder.` → reminder fires next day
5. `Rahul wali meeting 5 PM kar de.` → meeting updated, old reminder replaced
6. `Rahul wali meeting cancel kar.` → meeting + reminder cancelled
7. `Aaj ka summary de.` → daily report
8. `Weekly summary de.` → weekly report + AI insights
9. `Kal kya meetings hain?` → chronological list
10. `Pending followups bata.` → correct list
11. `Bhai kal 3 baje Manisha ke saath content meeting daal aur 30 min pehle remind kar.` → Hinglish meeting + custom reminder offset

## 11. Deployment (low-cost / free-friendly)

Any host that can run a long-lived Python process works — this app polls
Telegram and its own DB, so it needs to be **always-on**, not a serverless
function.

### Option A — Railway / Render (recommended, free tier available)
1. Push this repo to GitHub.
2. Create a new "Background Worker" / "Web Service" on Railway or Render.
3. Set the start command to `python main.py`.
4. Add all 5+ environment variables from `.env.example` in the platform's dashboard.
5. Deploy. The scheduler and bot run in the same process automatically.

### Option B — Fly.io
1. `fly launch` (choose "no" for a Postgres/Redis add-on, since you're using Supabase).
2. `fly secrets set TELEGRAM_BOT_TOKEN=... AI_API_KEY=... SUPABASE_URL=... SUPABASE_KEY=... TELEGRAM_USER_ID=...`
3. `fly deploy`

### Option C — A cheap always-on VM (e.g. Oracle free tier, small DigitalOcean droplet)
```bash
git clone <repo> && cd telegram-ai-assistant
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp .env.example .env  # fill in secrets
# run under a process manager so it survives reboots/crashes:
sudo apt install -y screen   # or use systemd/pm2
screen -S assistant
python main.py
# Ctrl+A then D to detach
```

A minimal `systemd` unit (`/etc/systemd/system/assistant.service`):
```ini
[Unit]
Description=Telegram AI Assistant
After=network.target

[Service]
WorkingDirectory=/path/to/telegram-ai-assistant
ExecStart=/path/to/telegram-ai-assistant/venv/bin/python main.py
EnvironmentFile=/path/to/telegram-ai-assistant/.env
Restart=always

[Install]
WantedBy=multi-user.target
```
Then: `sudo systemctl enable --now assistant`.

## 12. How the scheduler works

`app/reminders/scheduler.py` runs an `AsyncIOScheduler` (APScheduler) with
two jobs, started once the bot connects:
- A poll job (every `SCHEDULER_POLL_SECONDS`) that checks the `reminders`
  table for anything due and sends it via Telegram — this is the entire
  replacement for Google Calendar notifications.
- A weekly cron job that generates and sends the automatic performance report.

Both keep running as long as the process is alive, independent of whether
you're actively chatting.

## 13. AI cost control

The AI is called for exactly two things:
1. **Once per free-text message** to classify intent and extract entities (never chained/looped).
2. **Weekly insight generation** (once a week automatically, or on-demand via `/summary`).

Everything else — date/time math, statistics, meeting search, reminder
scheduling, slash commands — is plain deterministic Python with zero AI calls.

## 14. Security notes

- Every incoming update is checked against `TELEGRAM_USER_ID`; anyone else is rejected.
- All secrets are read from environment variables only (`app/config/settings.py`); nothing is hard-coded.
- `.env` is git-ignored — see `.gitignore`.
- Duplicate Telegram deliveries are de-duplicated via the `processed_messages` table before any write happens.

## 15. Extending later

The architecture is intentionally modular (`app/bot`, `app/ai`,
`app/database`, `app/reminders`, `app/analytics`, `app/reports`) so you can
add later, without touching the core:
- Gmail / WhatsApp channels (add a new handler module, reuse `app/ai/parser.py`)
- Real Instagram/LinkedIn analytics APIs (fill `content` table automatically instead of manually)
- Voice transcription (swap the stub in `handle_voice_message` for a Speech-to-Text call, then feed the text into the existing `handle_text_message` pipeline)
- A web dashboard (read the same Supabase tables)
- Multiple users (the schema already has a `users` table + `user_id` foreign keys everywhere; the only single-user shortcut is the `TELEGRAM_USER_ID` auth check)

**Do not** re-add Google Calendar to the core system — it was explicitly excluded by design.
