"""
Synchronisation eBay actif + Vinted + médiane pour toutes les cartes Pokédex.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from database import get_supabase
from ebay_browse import fetch_active_listings, filter_active_listings, stats_from_active_listings
from pricing import compute_reference_median
from product_keywords import resolve_market_keyword
from vinted_api import fetch_vinted_listings

logger = logging.getLogger(__name__)

MARKET_SYNC_DELAY_S = 1.5

_POKEDEX_MARKET_SELECT = (
    "id, nom, extension, langue, ebay_keyword, ebay_url, "
    "type_produit, numero_carte, code_set, nom_en, "
    "prix_actuel, tendance_7j, prix_reference_mediane"
)


async def sync_card_market_prices(card: dict[str, Any]) -> dict[str, Any]:
    """Fetch eBay Browse + Vinted, calcule médiane, met à jour Supabase."""
    from services import append_historique_market

    pid = str(card["id"])
    nom = card.get("nom") or ""
    keyword = resolve_market_keyword(card)
    langue = card.get("langue") or "FR"
    prix_cm = card.get("prix_actuel")
    old_median = card.get("prix_reference_mediane")

    ebay_stats = {"prix_moyen": None, "nb_annonces": 0}
    vinted_stats = {"prix_moyen_vinted": None, "nb_annonces_vinted": 0, "keyword": keyword}

    try:
        raw_listings = await fetch_active_listings(keyword)
        listings = filter_active_listings(raw_listings, card)
        ebay_stats = stats_from_active_listings(listings)
    except Exception as exc:
        logger.warning("eBay actif %s (%s): %s", nom or pid, keyword, exc)

    try:
        vinted_stats = await fetch_vinted_listings(langue=langue, card=card)
    except Exception as exc:
        logger.warning("Vinted %s (%s): %s", nom or pid, keyword, exc)

    nb_ebay = int(ebay_stats.get("nb_annonces") or 0)
    nb_vinted = int(vinted_stats.get("nb_annonces_vinted") or 0)
    prix_ebay = ebay_stats.get("prix_moyen") if nb_ebay > 0 else None
    prix_vinted = vinted_stats.get("prix_moyen_vinted") if nb_vinted > 0 else None
    prix_cm_f = float(prix_cm) if prix_cm is not None else None
    prix_ref = compute_reference_median(
        prix_cm_f,
        prix_ebay,
        prix_vinted,
        nb_ebay=nb_ebay,
        nb_vinted=nb_vinted,
    )
    now = datetime.now(timezone.utc).isoformat()

    update: dict[str, Any] = {
        "prix_reference_mediane": prix_ref,
        "prix_actif_ebay": prix_ebay,
        "nb_annonces_ebay_actif": nb_ebay,
        "date_maj_ebay_actif": now if nb_ebay > 0 else None,
        "prix_moyen_vinted": prix_vinted,
        "prix_min_vinted": vinted_stats.get("prix_min_vinted") if nb_vinted > 0 else None,
        "prix_max_vinted": vinted_stats.get("prix_max_vinted") if nb_vinted > 0 else None,
        "nb_annonces_vinted": nb_vinted,
        "date_maj_vinted": now if nb_vinted > 0 else None,
    }

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
        .select(_POKEDEX_MARKET_SELECT)
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
