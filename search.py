"""Recherche CardMarket par nom (page Search HTML)."""

from __future__ import annotations

import html
import logging
import re
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

from scraper import clean_cardmarket_name, parse_euro_price

logger = logging.getLogger(__name__)

CM_SEARCH_URL = "https://www.cardmarket.com/fr/Pokemon/Products/Search"
CM_BASE = "https://www.cardmarket.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
}


def _strip_tags(text: str) -> str:
    t = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return html.unescape(re.sub(r"\s+", " ", t)).strip()


def _parse_search_html(page_html: str, limit: int = 10) -> list[dict[str, Any]]:
    """Parse les lignes produit de la page Search CardMarket."""
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    # Blocs produit : lien vers /Pokemon/Products/ + nom + prix
    row_pattern = re.compile(
        r'<div[^>]*class="[^"]*row[^"]*"[^>]*>(.*?)</div>\s*</div>',
        re.I | re.DOTALL,
    )
    link_pattern = re.compile(
        r'href="(/fr/Pokemon/Products/[^"]+)"[^>]*>([^<]{2,120})</a>',
        re.I,
    )
    price_pattern = re.compile(
        r'(?:data-price|class="[^"]*price[^"]*")[^>]*>([^<]*\d[^<]*)</',
        re.I,
    )
    img_pattern = re.compile(
        r'src="(https?://[^"]*cardmarket[^"]*\.(?:jpg|jpeg|png|webp)[^"]*)"',
        re.I,
    )

    # Fallback : tous les liens produits
    for m in link_pattern.finditer(page_html):
        path = m.group(1).split("?")[0]
        if "/Products/" not in path:
            continue
        url = urljoin(CM_BASE, path)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        raw_name = _strip_tags(m.group(2))
        nom = clean_cardmarket_name(raw_name) or raw_name

        # Contexte autour du lien (prix / image)
        start = max(0, m.start() - 400)
        end = min(len(page_html), m.end() + 800)
        ctx = page_html[start:end]

        prix = None
        pm = price_pattern.search(ctx)
        if pm:
            prix = parse_euro_price(_strip_tags(pm.group(1)))

        image_url = None
        im = img_pattern.search(ctx)
        if im:
            image_url = im.group(1)

        # Extension depuis l'URL ou breadcrumb proche
        extension = ""
        ext_m = re.search(r"/Products/[^/]+/([^/]+)/", path)
        if ext_m:
            extension = ext_m.group(1).replace("-", " ")

        results.append(
            {
                "nom": nom,
                "extension": extension,
                "image_url": image_url,
                "prix_actuel": prix,
                "url_cardmarket": url,
            }
        )
        if len(results) >= limit:
            break

    return results


async def search_cardmarket(q: str, limit: int = 10) -> list[dict[str, Any]]:
    query = (q or "").strip()
    if len(query) < 2:
        return []

    url = CM_SEARCH_URL
    params = {"searchString": query, "idGame": "6", "idLanguage": "0"}

    try:
        async with httpx.AsyncClient(
            timeout=25.0,
            headers=_HEADERS,
            follow_redirects=True,
        ) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            items = _parse_search_html(response.text, limit=limit)
            logger.info("CM search %r → %s résultat(s)", query, len(items))
            return items
    except Exception as exc:
        logger.warning("CM search échec %r: %s", query, exc)
        return []
