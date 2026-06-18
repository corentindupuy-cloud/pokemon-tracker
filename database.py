"""Connexion Supabase."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env", override=True)


@lru_cache
def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL et SUPABASE_KEY requis dans .env")
    return create_client(url, key)
