from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from urllib.parse import urlencode

import httpx

from database import get_supabase

logger = logging.getLogger(__name__)

EBAY_SEARCH_URL = "https://www.ebay.fr/sch/i.html"
EBAY_CATEGORY_POKEMON = "183454"
EBAY_CACHE_HOURS = 6
EBAY_SLEEP_S = 2.0
EBAY_AVG_SAMPLE = 10

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
    "DNT": "1",
}

_FRENCH_MONTHS = {
    "janv": 1,
    "jan": 1,
    "janvier": 1,
    "févr": 2,
    "fevr": 2,
    "fév": 2,
    "fev": 2,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avr": 4,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juil": 7,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "sept": 9,
    "septembre": 9,
    "oct": 10,
    "octobre": 10,
    "nov": 11,
    "novembre": 11,
    "déc": 12,
    "dec": 12,
    "décembre": 12,
    "decembre": 12,
}

# Segments CardMarket à exclure des keywords (catégories produit, pas le nom commercial)
LANGUES_POKEDEX = frozenset({"FR", "EN", "JP", "IT", "DE", "ES"})

_LANGUE_EBAY_AUTO = {
    "FR": "French",
    "JP": "Japanese",
    "IT": "Italian",
}

_LANGUE_EBAY_MANUAL = {
    "FR": "French",
    "JP": "Japanese",
}

_SEALED_CATEGORY_PARTS = frozenset(
    {
        "box sets",
        "sealed products",
        "booster",
        "boosters",
        "booster box",
        "booster boxes",
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
    }
)

_SEALED_HINT_RE = re.compile(
    r"\b(?:booster\s*box|elite\s*trainer|etb|sealed|display|collection\s*box|"
    r"booster\s*pack|theme\s*deck|trainer\s*kit|tin)\b",
    re.I,
)

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
class EbaySearchPlan:
    search_url: Optional[str] = None
    keywords: Optional[str] = None
    source: Literal["url", "keyword", "auto"] = "auto"


@dataclass
class EbaySale:
    titre: str
    prix_vente: Optional[float]
    date_vente: Optional[datetime]
    categorie: Optional[str]
    url_ebay: Optional[str]


def _browser_headers() -> dict[str, str]:
    return dict(_BROWSER_HEADERS)


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    cleaned = re.sub(r"<style[\s\S]*?</style>", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_first(html_block: str, pattern: str) -> str:
    m = re.search(pattern, html_block, flags=re.I | re.DOTALL)
    if not m:
        return ""
    return _strip_html(m.group(1))


def _parse_price_text(text: str) -> Optional[float]:
    if not text:
        return None
    t = text.replace("\xa0", " ").replace("EUR", "€").strip()
    if re.search(r"\b(?:GBP|USD|\$|£)\b", t, re.I):
        usd_to_eur = float(os.getenv("USD_TO_EUR", "0.92"))
        m = re.search(r"([\d\s.,]+)", t)
        if not m:
            return None
        num = m.group(1).replace(" ", "").replace(",", ".")
        try:
            return round(float(num) * usd_to_eur, 2)
        except ValueError:
            return None
    m = re.search(r"([\d\s]+[.,]\d{2})|([\d\s]+)", t)
    if not m:
        return None
    num = (m.group(1) or m.group(2) or "").replace(" ", "").replace(",", ".")
    if num.count(".") > 1:
        parts = num.split(".")
        num = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return round(float(num), 2)
    except ValueError:
        return None


def _parse_ended_date(text: str) -> Optional[datetime]:
    if not text:
        return None
    t = _strip_html(text)
    t = re.sub(r"^(vendu|sold)\s+", "", t, flags=re.I).strip()

    m = re.search(
        r"(\d{1,2})\s+([a-zéû\.]+)\.?\s+(\d{4})",
        t,
        flags=re.I,
    )
    if m:
        day = int(m.group(1))
        month_key = m.group(2).lower().strip(".")
        year = int(m.group(3))
        month = _FRENCH_MONTHS.get(month_key)
        if month:
            try:
                return datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                return None

    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            return None

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _parse_sold_listings_html(page_html: str) -> list[EbaySale]:
    """Parse la page eBay « objets vendus » (s-item)."""
    blocks = re.findall(
        r'<li[^>]*class="[^"]*\bs-item\b[^"]*"[^>]*>(.*?)</li>',
        page_html,
        flags=re.I | re.DOTALL,
    )
    sales: list[EbaySale] = []
    seen_titles: set[str] = set()

    for block in blocks:
        title = _extract_first(
            block,
            r'class="[^"]*\bs-item__title\b[^"]*"[^>]*>(.*?)</(?:h3|div|span)',
        )
        if not title or title.lower().startswith("shop on ebay"):
            continue
        title_key = title.lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        price_raw = _extract_first(block, r'class="[^"]*\bs-item__price\b[^"]*"[^>]*>(.*?)</span>')
        price = _parse_price_text(price_raw)

        date_raw = _extract_first(
            block,
            r'class="[^"]*\bs-item__ended[-_]?date\b[^"]*"[^>]*>(.*?)</span>',
        )
        ended = _parse_ended_date(date_raw)

        url = _extract_first(block, r'class="[^"]*\bs-item__link\b[^"]*"[^>]*href="([^"]+)"')
        if url.startswith("//"):
            url = "https:" + url

        sales.append(
            EbaySale(
                titre=title,
                prix_vente=price,
                date_vente=ended,
                categorie=None,
                url_ebay=url or None,
            )
        )

    sales.sort(
        key=lambda s: s.date_vente or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return sales


def build_search_url(
    *,
    keywords: Optional[str] = None,
    category_id: str = EBAY_CATEGORY_POKEMON,
) -> str:
    params: dict[str, str] = {
        "LH_Sold": "1",
        "LH_Complete": "1",
        "_sacat": category_id,
    }
    if keywords:
        params["_nkw"] = keywords
    return f"{EBAY_SEARCH_URL}?{urlencode(params)}"


def _is_category_part(part: str) -> bool:
    p = part.strip().lower()
    if not p:
        return True
    if p in _EBAY_CATEGORY_PARTS:
        return True
    return bool(re.match(r"^(box\s*sets?|single?s?|sealed\s+products?)$", p, re.I))


def clean_nom_for_ebay(nom: str) -> str:
    """Extrait le nom commercial (sans Cardmarket ni catégories)."""
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

    if " | " in (nom or ""):
        return (nom or "").split(" | ", 1)[0].strip()
    return s


def clean_extension_for_ebay(extension: str) -> str:
    ext = (extension or "").strip()
    if not ext or _is_category_part(ext):
        return ""
    return ext


def _keyword_has_token(keyword: str, token: str) -> bool:
    return bool(re.search(rf"\b{re.escape(token)}\b", keyword, re.I))


def apply_langue_to_keyword(
    keyword: str,
    langue: str,
    *,
    manual: bool = False,
) -> str:
    """Ajoute le filtre langue au keyword (auto : FR/JP/IT ; manuel : FR/JP)."""
    lang = (langue or "FR").upper()
    tokens = _LANGUE_EBAY_MANUAL if manual else _LANGUE_EBAY_AUTO
    token = tokens.get(lang, "")
    if not token or _keyword_has_token(keyword, token):
        return keyword.strip()
    return f"{keyword.strip()} {token}".strip()


def is_sealed_product(nom: str, extension: str = "") -> bool:
    """Détecte un produit scellé depuis le nom / extension CardMarket."""
    for part in (nom or "").split(" | "):
        p = part.strip().lower()
        if p in _SEALED_CATEGORY_PARTS:
            return True
    combined = f"{nom or ''} {extension or ''}"
    return bool(_SEALED_HINT_RE.search(combined))


def build_keywords(nom: str, extension: str, langue: str = "FR") -> str:
    """Nom commercial + extension + Pokemon + filtre langue + sealed si besoin."""
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
    if is_sealed_product(nom, extension) and not _keyword_has_token(keyword, "sealed"):
        keyword = f"{keyword} sealed"
    return apply_langue_to_keyword(keyword, langue, manual=False)


def resolve_ebay_search(
    nom: str,
    extension: str = "",
    *,
    langue: str = "FR",
    ebay_keyword: Optional[str] = None,
    ebay_url: Optional[str] = None,
) -> EbaySearchPlan:
    """
    Priorité 1 : URL eBay custom
    Priorité 2 : keyword manuel (+ filtre langue FR/JP)
    Priorité 3 : auto depuis nom CardMarket
    """
    custom_url = (ebay_url or "").strip()
    if custom_url and re.search(r"ebay\.(com|fr|co\.uk|de|it|es)", custom_url, re.I):
        logger.info("eBay source=url custom")
        return EbaySearchPlan(search_url=custom_url, source="url")

    manual_kw = (ebay_keyword or "").strip()
    if manual_kw:
        kw = apply_langue_to_keyword(manual_kw, langue, manual=True)
        logger.info("eBay source=keyword manuel: %s", kw)
        return EbaySearchPlan(keywords=kw, source="keyword")

    kw = build_keywords(nom, extension, langue)
    logger.info("eBay source=auto: %s", kw)
    return EbaySearchPlan(keywords=kw, source="auto")


def _should_skip_keywords(keywords: str) -> bool:
    return not re.sub(r"\s+", "", keywords.replace("Pokemon", ""))


async def fetch_sold_items(
    *,
    keywords: Optional[str] = None,
    search_url: Optional[str] = None,
    category_id: str = EBAY_CATEGORY_POKEMON,
    days: Optional[int] = None,
) -> list[EbaySale]:
    """Scrape la page eBay « objets vendus » (httpx async, pas d'API Finding)."""
    if search_url:
        url = search_url
        logger.info("eBay URL custom: %s", url)
    else:
        url = build_search_url(keywords=keywords, category_id=category_id)
        if keywords:
            logger.info("eBay keyword: %s", keywords)

    async with httpx.AsyncClient(
        timeout=30.0,
        headers=_browser_headers(),
        follow_redirects=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        sales = _parse_sold_listings_html(response.text)

    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        dated = [s for s in sales if s.date_vente and s.date_vente >= cutoff]
        if dated:
            sales = dated

    logger.info(
        "eBay scrape: %s résultat(s) (%s)",
        len(sales),
        keywords or f"catégorie {category_id}",
    )
    return sales


# Alias compat main.py (trending)
fetch_completed_items = fetch_sold_items


def compute_stats(prices: list[float]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if not prices:
        return None, None, None
    return round(sum(prices) / len(prices), 2), round(min(prices), 2), round(max(prices), 2)


def stats_from_sales(
    sales: list[EbaySale],
    *,
    sample: int = EBAY_AVG_SAMPLE,
) -> tuple[Optional[float], Optional[float], Optional[float], int]:
    """Moyenne/min/max sur les N dernières ventes avec prix."""
    priced = [s.prix_vente for s in sales if s.prix_vente is not None and s.prix_vente > 0]
    if not priced:
        return None, None, None, 0
    top = priced[:sample]
    avg, mn, mx = compute_stats(top)
    return avg, mn, mx, len(priced)


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


async def sync_pokedex_sales(
    pokedex_id: str,
    nom: str,
    extension: str = "",
    *,
    langue: str = "FR",
    ebay_keyword: Optional[str] = None,
    ebay_url: Optional[str] = None,
) -> dict[str, Any]:
    """Sync ventes eBay via scraping HTML + met à jour champs eBay dans pokedex."""
    plan = resolve_ebay_search(
        nom,
        extension,
        langue=langue,
        ebay_keyword=ebay_keyword,
        ebay_url=ebay_url,
    )
    if plan.search_url:
        all_sales = await fetch_sold_items(search_url=plan.search_url)
        keywords = plan.keywords or ""
    else:
        keywords = plan.keywords or ""
        if _should_skip_keywords(keywords):
            raise RuntimeError("Nom/extension insuffisants pour eBay")
        all_sales = await fetch_sold_items(keywords=keywords, category_id=EBAY_CATEGORY_POKEMON)
    now = datetime.now(timezone.utc)
    cutoff_30 = now - timedelta(days=30)
    cutoff_7 = now - timedelta(days=7)

    sales_30 = [s for s in all_sales if not s.date_vente or s.date_vente >= cutoff_30]
    sales_7 = [s for s in all_sales if s.date_vente and s.date_vente >= cutoff_7]
    if not sales_30 and all_sales:
        sales_30 = all_sales

    avg30, min30, max30, nb30 = stats_from_sales(sales_30)
    avg7, min7, max7, _ = stats_from_sales(sales_7)

    store_sales(
        pokedex_id=pokedex_id,
        sales=sales_30,
        nb_ventes_7j=len(sales_7),
        prix_moyen_7j=avg7,
        prix_min_7j=min7,
        prix_max_7j=max7,
    )

    sb = get_supabase()
    sb.table("pokedex").update(
        {
            "prix_moyen_ebay": avg30,
            "prix_min_ebay": min30,
            "prix_max_ebay": max30,
            "nb_ventes_ebay": nb30,
            "date_maj_ebay": now.isoformat(),
        }
    ).eq("id", pokedex_id).execute()

    await asyncio.sleep(EBAY_SLEEP_S)

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
            "nb_ventes": nb30,
        },
        "stats_7j": {
            "prix_moyen": avg7,
            "prix_min": min7,
            "prix_max": max7,
            "nb_ventes": len(sales_7),
        },
        "keywords": keywords,
        "source": plan.source,
    }
