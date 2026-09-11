"""Deterministic contact/phone lookup for natural-language requests."""
from __future__ import annotations

import re

from app.database import repository as repo
from app.database.supabase_client import get_client


_NUMBER_WORDS = r"(?:number|mumber|phone|mobile|contact)"
_REQUEST_WORDS = r"(?:bhej(?:na)?|send|give|do|de(?:na)?|bata(?:na)?|bta(?:na)?|chahiye|\?)"


def is_contact_lookup(text: str) -> bool:
    """Return True for phone-number requests, but not updates/details."""
    value = text.strip().lower()
    if not value or re.search(r"\b(?:update|change|edit|details?)\b", value):
        return False
    return bool(re.search(rf"\b{_NUMBER_WORDS}\b", value) and re.search(_REQUEST_WORDS, value))


def _extract_names(text: str) -> list[str]:
    value = re.sub(r"[?!.]", " ", text.lower())
    match = re.search(rf"(.+?)\s+(?:ka|ki|ke)\s+{_NUMBER_WORDS}\b", value)
    if not match:
        match = re.search(rf"(.+?)\s+{_NUMBER_WORDS}\b", value)
    if not match:
        return []
    raw = re.sub(r"\b(?:bhej(?:na)?|send|give|do|de(?:na)?|bata(?:na)?|bta(?:na)?|chahiye)\b", " ", match.group(1))
    parts = re.split(r"\s+(?:aur|and)\s+|,|&", raw)
    return [re.sub(r"\s+", " ", part).strip() for part in parts if part.strip()]


def _find_phones(user_id: str, name: str) -> list[str]:
    db = get_client()
    rows = db.table("sales_calls").select("lead_name,phone_number,created_at").eq("user_id", user_id).execute().data or []
    needle = re.sub(r"\s+", " ", name.strip().lower())
    matches = []
    for row in rows:
        lead = re.sub(r"\s+", " ", (row.get("lead_name") or "").strip().lower())
        phone = (row.get("phone_number") or "").strip()
        if not lead or not phone:
            continue
        names = [p.strip().lower() for p in re.split(r",|&|\s+and\s+", lead) if p.strip()]
        if lead == needle or needle in names:
            matches.append((row.get("created_at") or "", phone))
            continue
        if re.search(rf"(?:^|\W){re.escape(needle)}(?:$|\W)", lead):
            matches.append((row.get("created_at") or "", phone))
    matches.sort(key=lambda item: item[0], reverse=True)
    phones = []
    for _, phone in matches:
        for candidate in re.split(r"[,;/]", phone):
            candidate = candidate.strip()
            if candidate and candidate not in phones:
                phones.append(candidate)
    return phones


async def handle_contact_lookup(message, user: dict, text: str) -> bool:
    if not is_contact_lookup(text):
        return False
    names = _extract_names(text)
    if not names:
        return False

    output = []
    for name in names:
        phones = _find_phones(user["id"], name)
        output.extend(phones)

    if output:
        await message.reply_text("\n".join(dict.fromkeys(output)))
    else:
        await message.reply_text("Number nahi mila.")
    return True
