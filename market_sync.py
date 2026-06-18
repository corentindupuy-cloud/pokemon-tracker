"""
Synchronisation eBay actif + Vinted + médiane pour toutes les cartes Pokédex.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from database import get_supabase
from ebay import resolve_ebay_search
from ebay_browse import fetch_active_listings, stats_from_active_listings
from pricing import compute_reference_median, market_keyword_for_card
from vinted_api import fetch_vinted_listings

logger = logging.getLogger(__name__)

MARKET_SYNC_DELAY_S = 1.5


def _keyword_from_ebay_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("_nkw", "q", "_skw"):
        if qs.get(key):
            return qs[key][0].replace("+", " ").strip() or None
    return None


def browse_keyword_for_card(card: dict[str, Any]) -> str:
    nom = card.get("nom") or ""
    ext = card.get("extension") or ""
    langue = card.get("langue") or "FR"
    ebay_kw = card.get("ebay_keyword")
    ebay_url = card.get("ebay_url")

    plan = resolve_ebay_search(
        nom,
        ext,
        langue=langue,
        ebay_keyword=ebay_kw,
        ebay_url=ebay_url,
    )
    if plan.keywords:
        return plan.keywords
    if plan.search_url:
        from_url = _keyword_from_ebay_url(plan.search_url)
        if from_url:
            return from_url
    return market_keyword_for_card(nom, ext, langue=langue, ebay_keyword=ebay_kw, ebay_url=ebay_url)


async def sync_card_market_prices(card: dict[str, Any]) -> dict[str, Any]:
    """Fetch eBay Browse + Vinted, calcule médiane, met à jour Supabase."""
    from services import append_historique_market

    pid = str(card["id"])
    nom = card.get("nom") or ""
    keyword = browse_keyword_for_card(card)
    langue = card.get("langue") or "FR"
    prix_cm = card.get("prix_actuel")
    old_median = card.get("prix_reference_mediane")

    ebay_stats = {"prix_moyen": None, "nb_annonces": 0}
    vinted_stats = {"prix_moyen_vinted": None, "nb_annonces_vinted": 0}

    try:
        listings = await fetch_active_listings(keyword)
        ebay_stats = stats_from_active_listings(listings)
    except Exception as exc:
        logger.warning("eBay actif %s (%s): %s", nom or pid, keyword, exc)

    try:
        vinted_stats = await fetch_vinted_listings(keyword, langue=langue)
    except Exception as exc:
        logger.warning("Vinted %s (%s): %s", nom or pid, keyword, exc)

    prix_ebay = ebay_stats.get("prix_moyen")
    prix_vinted = vinted_stats.get("prix_moyen_vinted")
    prix_cm_f = float(prix_cm) if prix_cm is not None else None
    prix_ref = compute_reference_median(prix_cm_f, prix_ebay, prix_vinted)
    now = datetime.now(timezone.utc).isoformat()

    update: dict[str, Any] = {"prix_reference_mediane": prix_ref}
    if prix_ebay is not None:
        update["prix_actif_ebay"] = prix_ebay
        update["nb_annonces_ebay_actif"] = ebay_stats.get("nb_annonces") or 0
        update["date_maj_ebay_actif"] = now
    if prix_vinted is not None:
        update["prix_moyen_vinted"] = prix_vinted
        update["prix_min_vinted"] = vinted_stats.get("prix_min_vinted")
        update["prix_max_vinted"] = vinted_stats.get("prix_max_vinted")
        update["nb_annonces_vinted"] = vinted_stats.get("nb_annonces_vinted") or 0
        update["date_maj_vinted"] = now

    sb = get_supabase()
    sb.table("pokedex").update(update).eq("id", pid).execute()

    if prix_cm_f is not None or prix_ebay is not None or prix_vinted is not None:
        append_historique_market(
            pid,
            prix_cm=prix_cm_f,
            prix_ebay_actif=prix_ebay,
            prix_vinted=prix_vinted,
            prix_mediane=prix_ref,
            tendance_7j=card.get("tendance_7j"),
            old_price=old_median if old_median is not None else prix_cm_f,
        )

    return {
        "pokedex_id": pid,
        "nom": nom,
        "keyword": keyword,
        "prix_actif_ebay": prix_ebay,
        "prix_moyen_vinted": prix_vinted,
        "prix_reference_mediane": prix_ref,
    }


async def sync_all_market_prices(*, delay_s: float = MARKET_SYNC_DELAY_S) -> dict[str, Any]:
    sb = get_supabase()
    cards = (
        sb.table("pokedex")
        .select(
            "id, nom, extension, langue, ebay_keyword, ebay_url, "
            "prix_actuel, tendance_7j, prix_reference_mediane"
        )
        .order("nom")
        .execute()
        .data
        or []
    )
    if not cards:
        return {"success": True, "synced": 0, "errors": []}

    ok, errors = 0, []
    for i, card in enumerate(cards):
        try:
            await sync_card_market_prices(card)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"id": card.get("id"), "nom": card.get("nom"), "error": str(exc)})
            logger.warning("Sync marché %s: %s", card.get("nom"), exc)
        if i < len(cards) - 1 and delay_s > 0:
            await asyncio.sleep(delay_s)

    from services import propagate_radar_urgency

    await propagate_radar_urgency()
    logger.info("Sync marché terminé: %s OK / %s erreurs", ok, len(errors))
    return {"success": True, "synced": ok, "total": len(cards), "errors": errors}
