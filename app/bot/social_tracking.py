"""Social tracking: backlog import, daily performance check-ins and follower audits."""
from __future__ import annotations
import datetime as dt
import re
from typing import Any
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from app.ai.parser import parse_message, resolve_datetime
from app.database import repository as repo
from app.config.settings import settings
SOCIAL_ACCOUNTS={"ig1":"Instagram 1","ig2":"Instagram 2","instagram1":"Instagram 1","instagram2":"Instagram 2","linkedin":"LinkedIn","li":"LinkedIn"}
PENDING_SOCIAL_STATES:dict[int,str]={}
def _tz(user:dict): return pytz.timezone(user.get("timezone") or settings.DEFAULT_TIMEZONE)
def _number(value:str|None)->int|None:
    if value is None:return None
    m=re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([km])?",str(value).lower().replace(",",""));
    if not m:return None
    n=float(m.group(1));return int(n*(1000 if m.group(2)=="k" else 1000000 if m.group(2)=="m" else 1))
def parse_follower_audit(text:str)->dict[str,int]:
    out={}
    for account,pat in [("Instagram 1",r"(?:ig1|instagram\s*1|instagram1)\s*[:=-]\s*([\d.,]+\s*[km]?)"),("Instagram 2",r"(?:ig2|instagram\s*2|instagram2)\s*[:=-]\s*([\d.,]+\s*[km]?)"),("LinkedIn",r"(?:li|linkedin)\s*[:=-]\s*([\d.,]+\s*[km]?)")]:
        m=re.search(pat,text,re.I);v=_number(m.group(1)) if m else None
        if v is not None:out[account]=v
    return out
def parse_performance_update(text:str)->dict[str,Any]:
    out={"reach":None,"views":None,"leads":None,"notes":text.strip()}
    for key,aliases in {"reach":r"reach|reached","views":r"views?|impressions?","leads":r"leads?|inquiries|enquiries"}.items():
        m=re.search(rf"(?:{aliases})\s*[:=-]?\s*([\d,.]+\s*[km]?)",text,re.I)
        if m:out[key]=_number(m.group(1))
    return out
def _platform_from_text(text:str):
    v=text.lower()
    if re.search(r"\b(?:ig1|instagram\s*1|instagram1)\b",v):return "Instagram","Instagram 1"
    if re.search(r"\b(?:ig2|instagram\s*2|instagram2)\b",v):return "Instagram","Instagram 2"
    if re.search(r"\b(?:linkedin|li)\b",v):return "LinkedIn","LinkedIn"
    if "instagram" in v or "insta" in v:return "Instagram","Instagram 1"
    if "linkedin" in v:return "LinkedIn","LinkedIn"
    return None,None
def _extract_date(text:str,tz)->str:
    now=dt.datetime.now(tz);m=re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b",text)
    if m:
        y=int(m.group(3) or now.year);y+=2000 if y<100 else 0
        try:return dt.date(y,int(m.group(2)),int(m.group(1))).isoformat()
        except ValueError:pass
    resolved=resolve_datetime(text,tz.zone,now=now);return resolved.date().isoformat() if resolved else now.date().isoformat()
def import_backlog(user_id:str,text:str,tz)->int:
    inserted=0
    for chunk in [x.strip() for x in re.split(r"\n\s*\n|\n",text) if x.strip()]:
        platform,account=_platform_from_text(chunk)
        try:parsed=parse_message(chunk)
        except Exception:parsed={}
        items=parsed.get("content_items") or [{}]
        for item in items:
            p=item.get("platform") or platform;a=item.get("account_name") or account
            if not p and not a:continue
            p="Instagram" if str(p).lower().startswith("insta") else "LinkedIn" if str(p).lower().startswith("link") else str(p)
            ctype=item.get("content_type") or "social_update";post_date=_extract_date(str(item.get("date") or chunk),tz)
            try:repo.log_content(user_id,p,ctype,post_date=post_date,account_name=a,title=item.get("title") or item.get("post_title") or chunk[:180],notes=chunk[:1000]);inserted+=1
            except Exception:
                repo.log_content(user_id,p,ctype,post_date=post_date,account_name=a);inserted+=1
    return inserted
def store_follower_audit(user_id:str,values:dict[str,int],captured_at:dt.datetime)->int:
    n=0
    for account,followers in values.items():repo.log_social_metric(user_id,"Instagram" if account.startswith("Instagram") else "LinkedIn",account,captured_at.date().isoformat(),followers=followers,notes="Weekly follower audit");n+=1
    return n
def store_performance_update(user_id:str,text:str,captured_at:dt.datetime)->int:
    d=parse_performance_update(text);p,a=_platform_from_text(text);targets=[(p,a)] if p else [("Instagram","Instagram 1"),("Instagram","Instagram 2"),("LinkedIn","LinkedIn")]
    for platform,account in targets:repo.log_social_metric(user_id,platform,account,captured_at.date().isoformat(),reach=d["reach"],views=d["views"],leads=d["leads"],notes=d["notes"][:1000])
    return len(targets)
def checkin_keyboard():return InlineKeyboardMarkup([[InlineKeyboardButton("📝 Send Quick Update",callback_data="social_checkin:quick"),InlineKeyboardButton("⏭ Nothing Today",callback_data="social_checkin:none")]])
def follower_prompt(chat_id:int|None=None)->str:
    if chat_id is not None:PENDING_SOCIAL_STATES[chat_id]="followers"
    return "📈  <b>WEEKLY AUDIT • FOLLOWER COUNTS</b>\n━━━━━━━━━━━━━━━━━━━━\nNaye week ke liye current follower numbers update kar do:\n\n1. Instagram Account 1:\n2. Instagram Account 2:\n3. LinkedIn:\n\nExample: <code>ig1: 12.4k, ig2: 4.5k, li: 3200</code>\n━━━━━━━━━━━━━━━━━━━━"
async def backlog_cmd(update:Update,context:ContextTypes.DEFAULT_TYPE):
    context.user_data["social_state"]="backlog";await update.message.reply_text("📥  <b>PAST SOCIAL MEDIA BACKLOG</b>\n━━━━━━━━━━━━━━━━━━━━\nPuraane jo bhi updates, posts ya activities log karni hain, sab ek saath neeche text mein bhej do.\n\n(Format matter nahi karta, main parse karke Supabase mein record kar doonga)\n━━━━━━━━━━━━━━━━━━━━",parse_mode="HTML")
async def handle_social_state(update:Update,context:ContextTypes.DEFAULT_TYPE)->bool:
    state=context.user_data.get("social_state") or PENDING_SOCIAL_STATES.get(update.effective_chat.id)
    if not state or not update.message or not update.message.text:return False
    user=repo.get_or_create_user(update.effective_user.id,update.effective_user.first_name);tz=_tz(user);text=update.message.text.strip()
    if state=="backlog":count=import_backlog(user["id"],text,tz);context.user_data.pop("social_state",None)
    elif state=="checkin":count=store_performance_update(user["id"],text,dt.datetime.now(tz));context.user_data.pop("social_state",None)
    elif state=="followers":
        values=parse_follower_audit(text)
        if len(values)<3:await update.message.reply_text("Please send all three values, e.g. ig1: 12.4k, ig2: 4.5k, li: 3200");return True
        count=store_follower_audit(user["id"],values,dt.datetime.now(tz));context.user_data.pop("social_state",None);PENDING_SOCIAL_STATES.pop(update.effective_chat.id,None)
    else:return False
    await update.message.reply_text(f"Social update saved: {count} records.");return True
async def handle_social_callback(update:Update,context:ContextTypes.DEFAULT_TYPE)->bool:
    data=update.callback_query.data or ""
    if data=="social_checkin:quick":
        await update.callback_query.answer();context.user_data["social_state"]="checkin";PENDING_SOCIAL_STATES[update.effective_chat.id]="checkin";await update.callback_query.message.reply_text("Send the 24-hour performance numbers and any important internal note. Example: reach 12k, views 8k, leads 3 — LinkedIn performed better than Instagram.");return True
    if data=="social_checkin:none":
        await update.callback_query.answer("No update recorded.");context.user_data.pop("social_state",None);PENDING_SOCIAL_STATES.pop(update.effective_chat.id,None);return True
    return False
async def send_daily_checkin(bot,chat_id:int):
    PENDING_SOCIAL_STATES[chat_id]="checkin"
    await bot.send_message(chat_id=chat_id,text="📊  <b>24-HOUR SOCIAL CHECK-IN</b>\n━━━━━━━━━━━━━━━━━━━━\nLast 24 hours mein kaisa performance raha?\n\n• Koi specific reach, views ya leads?\n• Content ke baare mein koi important observation ya update jo record karni hai?\n\n(Directly yahan reply kar do ya neeche button tap karo)\n━━━━━━━━━━━━━━━━━━━━",parse_mode="HTML",reply_markup=checkin_keyboard())
