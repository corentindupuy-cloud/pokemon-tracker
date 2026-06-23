"""
eBay Browse REST API — annonces actives (OAuth2 Client Credentials).

Les ventes terminées (sold) ne sont pas disponibles via Browse API ;
elles restent sur le scraping HTML (module ebay.py).

Variables .env :
  EBAY_CLIENT_ID
  EBAY_CLIENT_SECRET
  EBAY_MARKETPLACE_ID  (défaut EBAY_FR)
  USD_TO_EUR           (conversion si prix en USD)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from product_keywords import build_keyword, min_price_for_type, title_matches, title_tokens_for_card

logger = logging.getLogger(__name__)

EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
EBAY_OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
EBAY_CATEGORY_POKEMON = "183454"
EBAY_BROWSE_CONDITION_FILTER = "conditionIds:{3000|4000|5000}"
EBAY_BROWSE_DEFAULT_LIMIT = 50
EBAY_TOKEN_TTL_S = 7200
EBAY_TOKEN_REFRESH_BUFFER_S = 120

_token_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}
_token_lock = asyncio.Lock()


@dataclass
class EbayActiveListing:
    """Annonce active (Browse API — pas une vente terminée)."""

    titre: str
    prix: Optional[float]
    devise: str
    url_ebay: Optional[str]
    image_url: Optional[str]
    item_id: Optional[str]
    condition: Optional[str]


def _client_credentials() -> tuple[str, str]:
    client_id = (os.getenv("EBAY_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("EBAY_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "EBAY_CLIENT_ID et EBAY_CLIENT_SECRET requis pour la Browse API"
        )
    return client_id, client_secret


def _marketplace_id() -> str:
    return (os.getenv("EBAY_MARKETPLACE_ID") or "EBAY_FR").strip() or "EBAY_FR"


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _price_to_eur(value: str | float | int, currency: str) -> Optional[float]:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    cur = (currency or "EUR").upper()
    if cur == "EUR":
        return round(amount, 2)
    if cur == "USD":
        rate = float(os.getenv("USD_TO_EUR", "0.92"))
        return round(amount * rate, 2)
    return round(amount, 2)


async def get_access_token(*, force_refresh: bool = False) -> str:
    """
    OAuth2 Client Credentials — token mis en cache (~7200 s).
    """
    now = time.time()
    cached = _token_cache.get("access_token")
    expires_at = float(_token_cache.get("expires_at") or 0)

    if not force_refresh and cached and now < expires_at - EBAY_TOKEN_REFRESH_BUFFER_S:
        return str(cached)

    async with _token_lock:
        now = time.time()
        cached = _token_cache.get("access_token")
        expires_at = float(_token_cache.get("expires_at") or 0)
        if not force_refresh and cached and now < expires_at - EBAY_TOKEN_REFRESH_BUFFER_S:
            return str(cached)

        client_id, client_secret = _client_credentials()
        logger.info("eBay Browse: demande access_token OAuth2")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                EBAY_OAUTH_URL,
                headers={
                    "Authorization": _basic_auth_header(client_id, client_secret),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": EBAY_OAUTH_SCOPE,
                },
            )
            response.raise_for_status()
            data = response.json()

        token = data.get("access_token")
        if not token:
            raise RuntimeError("eBay OAuth: access_token absent dans la réponse")

        expires_in = int(data.get("expires_in") or EBAY_TOKEN_TTL_S)
        _token_cache["access_token"] = token
        _token_cache["expires_at"] = time.time() + expires_in
        logger.info("eBay Browse: token obtenu (expire dans %ss)", expires_in)
        return str(token)


def _parse_active_item(raw: dict[str, Any]) -> EbayActiveListing:
    price_obj = raw.get("price") or {}
    value = price_obj.get("value")
    currency = str(price_obj.get("currency") or "EUR")
    image = raw.get("image") or {}
    return EbayActiveListing(
        titre=str(raw.get("title") or "").strip(),
        prix=_price_to_eur(value, currency) if value is not None else None,
        devise=currency,
        url_ebay=raw.get("itemWebUrl") or raw.get("itemAffiliateWebUrl"),
        image_url=image.get("imageUrl"),
        item_id=raw.get("itemId"),
        condition=raw.get("condition"),
    )


async def fetch_active_listings(
    keyword: str,
    *,
    limit: int = EBAY_BROWSE_DEFAULT_LIMIT,
    category_id: str = EBAY_CATEGORY_POKEMON,
    sort: str = "price",
) -> list[EbayActiveListing]:
    """
    Annonces actives via Browse API (prix demandés, pas sold).

    GET /buy/browse/v1/item_summary/search
    """
    q = (keyword or "").strip()
    if not q:
        return []

    token = await get_access_token()
    params = {
        "q": q,
        "category_ids": category_id,
        "filter": EBAY_BROWSE_CONDITION_FILTER,
        "sort": sort,
        "limit": str(min(max(limit, 1), 200)),
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": _marketplace_id(),
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            EBAY_BROWSE_SEARCH_URL,
            params=params,
            headers=headers,
        )

        if response.status_code == 401:
            logger.warning("eBay Browse: 401 — refresh token")
            token = await get_access_token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            response = await client.get(
                EBAY_BROWSE_SEARCH_URL,
                params=params,
                headers=headers,
            )

        response.raise_for_status()
        payload = response.json()

    summaries = payload.get("itemSummaries") or []
    listings = [_parse_active_item(item) for item in summaries if item.get("title")]
    logger.info(
        "eBay Browse: %s annonce(s) active(s) pour %r (total %s)",
        len(listings),
        q,
        payload.get("total", "?"),
    )
    return listings


def filter_active_listings(
    listings: list[EbayActiveListing],
    card: dict[str, Any],
) -> list[EbayActiveListing]:
    """Filtre prix minimum et pertinence du titre selon le type produit."""
    min_price = min_price_for_type(card.get("type_produit", "single"))
    tokens = title_tokens_for_card(card)
    out: list[EbayActiveListing] = []
    for listing in listings:
        if listing.prix is None or listing.prix < min_price:
            continue
        if tokens and not title_matches(listing.titre, tokens):
            continue
        out.append(listing)
    return out


async def fetch_sold_listings(
    keyword: str,
    *,
    search_url: Optional[str] = None,
    category_id: Optional[str] = None,
    days: Optional[int] = None,
) -> list[Any]:
    """
    Ventes terminées — délègue au scraping HTML (ebay.fetch_sold_items).

    Browse API ne fournit pas les sold listings.
    Marketplace Insights API nécessite un accès restreint.
    """
    from ebay import EBAY_CATEGORY_POKEMON, EBAY_SOLD_DAYS, EbaySale, fetch_sold_items

    cat = category_id or EBAY_CATEGORY_POKEMON
    window = days if days is not None else EBAY_SOLD_DAYS

    if search_url:
        sales: list[EbaySale] = await fetch_sold_items(
            search_url=search_url,
            days=window,
        )
    else:
        kw = (keyword or "").strip()
        if not kw.replace("Pokemon", "").strip():
            logger.warning("eBay sold: keyword vide")
            return []
        sales = await fetch_sold_items(
            keywords=kw,
            category_id=cat,
            days=window,
        )

    logger.info("eBay sold (scrape): %s vente(s) pour %r", len(sales), keyword or search_url)
    return sales


def stats_from_active_listings(
    listings: list[EbayActiveListing],
) -> dict[str, Any]:
    """Stats sur les prix demandés (annonces actives retournées par l'API)."""
    nb = len(listings)
    empty = {
        "prix_moyen": None,
        "prix_min": None,
        "prix_max": None,
        "nb_annonces": nb,
        "source": "browse_active",
    }
    if nb == 0:
        return empty

    prices = [l.prix for l in listings if l.prix is not None and l.prix > 0]
    if not prices:
        return empty

    return {
        "prix_moyen": round(sum(prices) / len(prices), 2),
        "prix_min": round(min(prices), 2),
        "prix_max": round(max(prices), 2),
        "nb_annonces": nb,
        "source": "browse_active",
    }
