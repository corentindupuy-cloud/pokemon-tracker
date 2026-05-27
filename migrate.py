#!/usr/bin/env python3
"""
Importe les données du Google Sheet Pokémon Tracker vers Supabase.

Usage:
  python migrate.py

Variables .env requises:
  SUPABASE_URL, SUPABASE_KEY
  GOOGLE_CREDENTIALS_PATH, SPREADSHEET_ID
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
import gspread

from database import get_supabase

load_dotenv()

SHEET_POKEDEX = os.getenv("SHEET_TAB", "Pokédex")
SHEET_STOCK = "📦 Stock"
SHEET_HISTORIQUE = "📈 Historique"
DATA_START = 5
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def parse_price(text: str) -> float | None:
    if not text:
        return None
    cleaned = str(text).replace("€", "").replace(",", ".").strip()
    m = re.search(r"[\d.]+", cleaned)
    return float(m.group()) if m else None


def parse_date_fr(text: str) -> str | None:
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip()[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def get_sheet():
    creds = Credentials.from_service_account_file(
        os.environ["GOOGLE_CREDENTIALS_PATH"], scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(os.environ["SPREADSHEET_ID"])


def migrate_pokedex(sb, sh) -> dict[str, str]:
    """Retourne mapping nom → pokedex_id."""
    ws = sh.worksheet(SHEET_POKEDEX)
    rows = ws.get_all_values()
    name_to_id: dict[str, str] = {}
    count = 0

    for i, row in enumerate(rows, start=1):
        if i < DATA_START:
            continue
        padded = row + [""] * 9
        nom = padded[0].strip()
        if not nom:
            continue
        url = padded[8].strip() if "cardmarket.com" in padded[8] else f"https://placeholder.local/{nom}"
        data = {
            "nom": nom,
            "extension": padded[1].strip(),
            "etat": padded[2].strip() or "Near Mint",
            "url_cardmarket": url.split("?")[0],
            "prix_actuel": parse_price(padded[3]),
            "tendance_7j": parse_price(padded[4]),
        }
        try:
            ins = sb.table("pokedex").insert(data).execute()
            pid = ins.data[0]["id"]
            name_to_id[nom] = pid
            count += 1
        except Exception as exc:
            print(f"  Skip {nom}: {exc}")

    print(f"Pokédex : {count} cartes importées")
    return name_to_id


def migrate_stock(sb, sh, name_to_id: dict[str, str]) -> None:
    try:
        ws = sh.worksheet(SHEET_STOCK)
    except gspread.WorksheetNotFound:
        print("Stock : onglet absent")
        return

    rows = ws.get_all_values()
    count = 0
    for i, row in enumerate(rows, start=1):
        if i < DATA_START:
            continue
        padded = row + [""] * 8
        nom = padded[1].strip()  # col B
        if not nom:
            continue
        pid = name_to_id.get(nom)
        data = {
            "pokedex_id": pid,
            "ref": padded[0].strip() or None,
            "prix_achat": parse_price(padded[3]),
            "date_achat": parse_date_fr(padded[4] if len(padded) > 4 else ""),
            "statut": padded[5].strip() if len(padded) > 5 else "En stock",
            "source": padded[6].strip() if len(padded) > 6 else None,
            "quantite": int(padded[7]) if len(padded) > 7 and str(padded[7]).isdigit() else 1,
        }
        if not data["prix_achat"]:
            continue
        sb.table("stock").insert(data).execute()
        count += 1
    print(f"Stock : {count} lignes importées")


def migrate_historique(sb, sh, name_to_id: dict[str, str]) -> None:
    try:
        ws = sh.worksheet(SHEET_HISTORIQUE)
    except gspread.WorksheetNotFound:
        print("Historique : onglet absent")
        return

    rows = ws.get_all_values()
    count = 0
    for row in rows[1:]:
        padded = row + [""] * 10
        nom = padded[1].strip()
        pid = name_to_id.get(nom)
        if not pid:
            continue
        prix = parse_price(padded[4])
        if prix is None:
            continue
        data = {
            "pokedex_id": pid,
            "prix": float(prix),
            "tendance_7j": parse_price(padded[5]),
            "variation_j1_eur": parse_price(padded[6]),
            "variation_j1_pct": parse_price(str(padded[7]).replace("%", "")),
            "variation_j0_eur": parse_price(padded[8]),
            "variation_j0_pct": parse_price(str(padded[9]).replace("%", "")),
            "date": parse_date_fr(padded[0]) or datetime.now().date().isoformat(),
        }
        sb.table("historique_prix").insert(data).execute()
        count += 1
    print(f"Historique : {count} entrées importées")


def main() -> None:
    sb = get_supabase()
    sh = get_sheet()
    print("Migration Google Sheets → Supabase")
    name_to_id = migrate_pokedex(sb, sh)
    migrate_stock(sb, sh, name_to_id)
    migrate_historique(sb, sh, name_to_id)
    print("Terminé.")


if __name__ == "__main__":
    main()
