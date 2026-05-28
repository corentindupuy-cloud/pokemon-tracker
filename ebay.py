from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from database import get_supabase

logger = logging.getLogger(__name__)

EBAY_FINDING_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
EBAY_CACHE_HOURS = 6
EBAY_SLEEP_S = 2.0
EBAY_API_DELAY_S = 2.0

# Segments CardMarket à exclure des keywords (catégories produit, pas le nom commercial)
_EBAY_CATEGORY_PARTS = frozenset(
    {
        "box sets",
        "singles",
        "booster",
        "boosters",
        "booster box",
        "booster boxes",
        "sealed products",
        "display",
        "theme deck",
        "theme decks",
        "trainer kits",
        "elite trainer box",
        "etb",
        "collection",
        "collections",
        "tin",
        "tins",
        "lots",
        "unsold lots",
        "playmats",
        "accessories",
        "cardmarket",
        "card market",
    }
)


@dataclass
class EbaySale:
    titre: str
    prix_vente: Optional[float]
    date_vente: Optional[datetime]
    categorie: Optional[str]
    url_ebay: Optional[str]


def _first(v: Any) -> Any:
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # ex: "2026-05-27T10:22:43.000Z"
        if ts.endswith("Z"):
            ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _parse_price(price_node: Any, usd_to_eur: float) -> Optional[float]:
    node = _first(price_node)
    if not isinstance(node, dict):
        return None
    raw = node.get("__value__") or node.get("value")
    if raw is None:
        return None
    try:
        amount = float(str(raw).replace(",", "."))
    except ValueError:
        return None
    currency = (node.get("@currencyId") or node.get("currencyId") or "EUR").upper()
    if currency == "EUR":
        return amount
    if currency == "USD":
        return round(amount * usd_to_eur, 2)
    return amount


def _extract_category(item: dict[str, Any]) -> Optional[str]:
    cat = _first(item.get("primaryCategory"))
    if isinstance(cat, dict):
        return _first(cat.get("categoryName"))
    return None


def _extract_url(item: dict[str, Any]) -> Optional[str]:
    return _first(item.get("viewItemURL")) or _first(item.get("viewItemUrl")) or None


def _parse_completed_items(payload: dict[str, Any], usd_to_eur: float) -> list[EbaySale]:
    root = _first(payload.get("findCompletedItemsResponse"))
    if not isinstance(root, dict):
        return []
    ack = str(_first(root.get("ack")) or "").lower()
    if ack and ack not in ("success", "warning"):
        return []
    search = _first(root.get("searchResult"))
    if not isinstance(search, dict):
        return []
    items = search.get("item") or []
    if isinstance(items, dict):
        items = [items]

    out: list[EbaySale] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(_first(item.get("title")) or "").strip()
        if not title:
            continue
        status = _first(item.get("sellingStatus"))
        price = None
        if isinstance(status, dict):
            price = _parse_price(status.get("currentPrice"), usd_to_eur)
        end_time = _parse_iso(_first(item.get("endTime")))
        out.append(
            EbaySale(
                titre=title,
                prix_vente=price,
                date_vente=end_time,
                categorie=_extract_category(item),
                url_ebay=_extract_url(item),
            )
        )
    # tri desc (dernier vendu en premier)
    out.sort(key=lambda s: s.date_vente or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out


def _ebay_app_id() -> str:
    # Nouvelle convention demandée
    app_id = os.getenv("EBAY_APP_ID", "").strip()
    if app_id:
        return app_id
    # Compat: ancienne variable
    return os.getenv("EBAY_API_KEY", "").strip()


def _is_category_part(part: str) -> bool:
    p = part.strip().lower()
    if not p:
        return True
    if p in _EBAY_CATEGORY_PARTS:
        return True
    return bool(re.match(r"^(box\s*sets?|single?s?|sealed\s+products?)$", p, re.I))


def clean_nom_for_ebay(nom: str) -> str:
    """
    Extrait le nom commercial : retire « | Cardmarket », catégories (Box Sets…),
    ne garde que le premier segment utile.
    """
    s = (nom or "").strip()
    s = re.sub(r"\s*\|\s*card\s*market\s*", "", s, flags=re.I).strip()

    if " | " in s:
        segments = [p.strip() for p in s.split(" | ") if p.strip()]
    else:
        segments = [s] if s else []

    for part in segments:
        if _is_category_part(part):
            continue
        if re.match(r"^\s*card\s*market\s*$", part, re.I):
            continue
        return part

    # repli : tout avant le premier « | »
    if " | " in (nom or ""):
        return (nom or "").split(" | ", 1)[0].strip()
    return s


def clean_extension_for_ebay(extension: str) -> str:
    ext = (extension or "").strip()
    if not ext or _is_category_part(ext):
        return ""
    return ext


def build_keywords(nom: str, extension: str) -> str:
    """Nom commercial + extension (set) + Pokemon pour la recherche eBay."""
    parts: list[str] = []
    name = clean_nom_for_ebay(nom)
    if name:
        parts.append(name)
    ext = clean_extension_for_ebay(extension)
    if ext and ext.lower() != name.lower():
        parts.append(ext)
    keyword = " ".join(parts).strip()
    if keyword and "pokemon" not in keyword.lower():
        keyword = f"{keyword} Pokemon"
    elif not keyword:
        keyword = "Pokemon"
    return keyword


def _should_skip_keywords(keywords: str) -> bool:
    return not re.sub(r"\s+", "", keywords.replace("Pokemon", ""))


def fetch_completed_items(
    *,
    keywords: Optional[str],
    category_id: str,
    days: int,
    sold_only: bool = True,
    entries_per_page: int = 100,
) -> list[EbaySale]:
    app_id = _ebay_app_id()
    if not app_id:
        raise RuntimeError("EBAY_APP_ID manquant")

    now = datetime.now(timezone.utc)
    end_from = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    usd_to_eur = float(os.getenv("USD_TO_EUR", "0.92"))

    params: dict[str, str] = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": app_id,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "categoryId": category_id,
        "GLOBAL-ID": "EBAY-FR",
        "sortOrder": "EndTimeSoonest",
        "paginationInput.entriesPerPage": str(entries_per_page),
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true" if sold_only else "false",
        "itemFilter(1).name": "EndTimeFrom",
        "itemFilter(1).value": end_from,
    }
    if keywords:
        logger.info(f"eBay keyword: {keywords}")
        params["keywords"] = keywords

    with httpx.Client(timeout=25.0) as client:
        r = client.get(EBAY_FINDING_URL, params=params)
        r.raise_for_status()
        payload = r.json()
    return _parse_completed_items(payload, usd_to_eur)


def get_cached_sales(pokedex_id: str) -> Optional[list[dict[str, Any]]]:
    """Retourne les lignes ebay_sales récentes si le cache 6h est valide."""
    sb = get_supabase()
    since = (datetime.now(timezone.utc) - timedelta(hours=EBAY_CACHE_HOURS)).isoformat()
    rows = (
        sb.table("ebay_sales")
        .select("*")
        .eq("pokedex_id", pokedex_id)
        .gte("created_at", since)
        .order("date_vente", desc=True)
        .limit(200)
        .execute()
        .data
        or []
    )
    return rows or None


def store_sales(
    *,
    pokedex_id: Optional[str],
    sales: list[EbaySale],
    nb_ventes_7j: Optional[int] = None,
    prix_moyen_7j: Optional[float] = None,
    prix_min_7j: Optional[float] = None,
    prix_max_7j: Optional[float] = None,
) -> int:
    if not sales:
        return 0
    sb = get_supabase()
    payload: list[dict[str, Any]] = []
    for s in sales:
        payload.append(
            {
                "pokedex_id": pokedex_id,
                "titre": s.titre,
                "prix_vente": s.prix_vente,
                "date_vente": s.date_vente.isoformat() if s.date_vente else None,
                "categorie": s.categorie,
                "url_ebay": s.url_ebay,
                "nb_ventes_7j": nb_ventes_7j,
                "prix_moyen_7j": prix_moyen_7j,
                "prix_min_7j": prix_min_7j,
                "prix_max_7j": prix_max_7j,
            }
        )
    sb.table("ebay_sales").insert(payload).execute()
    return len(payload)


def compute_stats(prices: list[float]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if not prices:
        return None, None, None
    return round(sum(prices) / len(prices), 2), round(min(prices), 2), round(max(prices), 2)


def sync_pokedex_sales(pokedex_id: str, nom: str, extension: str) -> dict[str, Any]:
    """Sync ventes eBay 30j pour une carte + met à jour champs eBay dans pokedex."""
    keywords = build_keywords(nom, extension)
    if _should_skip_keywords(keywords):
        raise RuntimeError("Nom/extension insuffisants pour eBay")

    sales_30 = fetch_completed_items(keywords=keywords, category_id="183454", days=30)
    prices_30 = [s.prix_vente for s in sales_30 if s.prix_vente]
    avg30, min30, max30 = compute_stats(prices_30[:100])

    time.sleep(EBAY_API_DELAY_S)
    sales_7 = fetch_completed_items(keywords=keywords, category_id="183454", days=7)
    prices_7 = [s.prix_vente for s in sales_7 if s.prix_vente]
    avg7, min7, max7 = compute_stats(prices_7[:100])

    # store ventes (on stocke la liste 30j)
    store_sales(
        pokedex_id=pokedex_id,
        sales=sales_30,
        nb_ventes_7j=len(sales_7),
        prix_moyen_7j=avg7,
        prix_min_7j=min7,
        prix_max_7j=max7,
    )

    # update pokedex
    sb = get_supabase()
    sb.table("pokedex").update(
        {
            "prix_moyen_ebay": avg30,
            "prix_min_ebay": min30,
            "prix_max_ebay": max30,
            "nb_ventes_ebay": len(sales_30),
            "date_maj_ebay": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", pokedex_id).execute()

    # throttling quota (sync cartes en rafale)
    time.sleep(EBAY_SLEEP_S)

    return {
        "sales": [
            {
                "titre": s.titre,
                "prix_vente": s.prix_vente,
                "date_vente": s.date_vente.isoformat() if s.date_vente else None,
                "categorie": s.categorie,
                "url_ebay": s.url_ebay,
            }
            for s in sales_30
        ],
        "stats_30j": {
            "prix_moyen": avg30,
            "prix_min": min30,
            "prix_max": max30,
            "nb_ventes": len(sales_30),
        },
        "stats_7j": {
            "prix_moyen": avg7,
            "prix_min": min7,
            "prix_max": max7,
            "nb_ventes": len(sales_7),
        },
        "keywords": keywords,
    }

