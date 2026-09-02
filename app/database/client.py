"""Thin wrapper around the Supabase client (single shared instance)."""
from supabase import create_client, Client
from app.config.settings import settings

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _client
