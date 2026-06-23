"""
Vinted — annonces actives via vinted-api-kit.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote_plus

from vinted import VintedClient

from product_keywords import (
    build_keyword,
    min_price_for_type,
    resolve_market_keyword,
    title_matches,
    title_tokens_for_card,
)

logger = logging.getLogger(__name__)

_VINTED_DOMAIN_BY_LANG = {
    "FR": "fr",
    "IT": "it",
    "DE": "de",
    "ES": "es",
    "EN": "com",
    "JP": "com",
}


def vinted_domain(langue: str = "FR") -> str:
    lang = (langue or "FR").upper()
    if lang in ("FR", "IT", "DE", "ES"):
        return _VINTED_DOMAIN_BY_LANG.get(lang, "fr")
    return "com"


def build_vinted_search_url(keyword: str, langue: str = "FR") -> str:
    domain = vinted_domain(langue)
    q = quote_plus((keyword or "").strip())
    return f"https://www.vinted.{domain}/catalog?search_text={q}&order=price_low_to_high"


def _filter_vinted_item(
    item: Any,
    *,
    min_price: float,
    title_tokens: list[str],
) -> Optional[dict[str, Any]]:
    titre = str(getattr(item, "title", "") or "").strip()
    if not titre:
        return None
    if title_tokens and not title_matches(titre, title_tokens):
        return None
    try:
        price = float(item.price) if item.price is not None else None
    except (TypeError, ValueError):
        price = None
    if price is None or price < min_price:
        return None
    return {
        "titre": titre,
        "prix": round(price, 2),
        "url": getattr(item, "url", None),
    }


async def fetch_vinted_listings(
    keyword: str | None = None,
    langue: str = "FR",
    *,
    card: Optional[dict[str, Any]] = None,
    per_page: int = 20,
) -> dict[str, Any]:
    """Recherche Vinted et stats prix sur annonces filtrées."""
    card = card or {}
    kw = (keyword or resolve_market_keyword(card) or build_keyword(card)).strip()
    empty = {
        "prix_moyen_vinted": None,
        "prix_min_vinted": None,
        "prix_max_vinted": None,
        "nb_annonces_vinted": 0,
        "listings": [],
        "keyword": kw,
    }
    if not kw:
        return empty

    url = build_vinted_search_url(kw, langue or card.get("langue") or "FR")
    try:
        async with VintedClient() as client:
            items = await client.search_items(url=url, per_page=per_page)
    except Exception as exc:
        logger.warning("Vinted search %r: %s", kw, exc)
        raise RuntimeError(f"Vinted indisponible: {exc}") from exc

    min_price = min_price_for_type(card.get("type_produit", "single"))
    title_tokens = title_tokens_for_card(card)
    filtered: list[dict[str, Any]] = []
    for item in items:
        row = _filter_vinted_item(item, min_price=min_price, title_tokens=title_tokens)
        if row:
            filtered.append(row)

    prices = [r["prix"] for r in filtered]
    nb = len(filtered)
    logger.info(
        "Vinted: %s/%s annonce(s) retenue(s) pour %r (min %.0f€)",
        nb,
        len(items),
        kw,
        min_price,
    )
    return {
        "prix_moyen_vinted": round(sum(prices) / len(prices), 2) if prices else None,
        "prix_min_vinted": min(prices) if prices else None,
        "prix_max_vinted": max(prices) if prices else None,
        "nb_annonces_vinted": nb,
        "listings": filtered[:10],
        "keyword": kw,
    }
