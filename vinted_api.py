"""
Vinted — annonces actives via vinted-api-kit.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote_plus

from vinted import VintedClient

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


async def fetch_vinted_listings(keyword: str, langue: str = "FR", *, per_page: int = 20) -> dict[str, Any]:
    """Recherche Vinted et stats prix (moyenne / min / max)."""
    kw = (keyword or "").strip()
    if not kw:
        return {
            "prix_moyen_vinted": None,
            "prix_min_vinted": None,
            "prix_max_vinted": None,
            "nb_annonces_vinted": 0,
            "listings": [],
        }

    url = build_vinted_search_url(kw, langue)
    try:
        async with VintedClient() as client:
            items = await client.search_items(url=url, per_page=per_page)
    except Exception as exc:
        logger.warning("Vinted search %r: %s", kw, exc)
        raise RuntimeError(f"Vinted indisponible: {exc}") from exc

    prices: list[float] = []
    listings: list[dict[str, Any]] = []
    for item in items:
        try:
            price = float(item.price) if item.price is not None else None
        except (TypeError, ValueError):
            price = None
        if price is not None and price > 0:
            prices.append(price)
        if len(listings) < 10:
            listings.append(
                {
                    "titre": str(getattr(item, "title", "") or ""),
                    "prix": price,
                    "url": getattr(item, "url", None),
                }
            )

    logger.info("Vinted: %s annonce(s) pour %r", len(prices), kw)
    return {
        "prix_moyen_vinted": round(sum(prices) / len(prices), 2) if prices else None,
        "prix_min_vinted": min(prices) if prices else None,
        "prix_max_vinted": max(prices) if prices else None,
        "nb_annonces_vinted": len(prices),
        "listings": listings,
    }
