"""Calculs prix de référence (médiane multi-sources) et scores d'opportunité."""

from __future__ import annotations

from typing import Any, Optional

from product_keywords import resolve_market_keyword


MIN_LISTINGS_PER_SOURCE = 3
MIN_SOURCES_FOR_MEDIAN = 2


def median_price(*values: Optional[float]) -> Optional[float]:
    nums = sorted(v for v in values if v is not None and v > 0)
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return round(nums[mid], 2)
    return round((nums[mid - 1] + nums[mid]) / 2, 2)


def market_keyword_for_card(
    card: dict[str, Any],
) -> str:
    """Mot-clé de recherche marché — priorité keyword/URL manuels puis build_keyword."""
    return resolve_market_keyword(card)


def market_keyword_for_card_legacy(
    nom: str,
    extension: str = "",
    *,
    langue: str = "FR",
    ebay_keyword: Optional[str] = None,
    ebay_url: Optional[str] = None,
    type_produit: str = "single",
    numero_carte: Optional[str] = None,
    code_set: Optional[str] = None,
    nom_en: Optional[str] = None,
) -> str:
    return resolve_market_keyword(
        {
            "nom": nom,
            "extension": extension,
            "langue": langue,
            "ebay_keyword": ebay_keyword,
            "ebay_url": ebay_url,
            "type_produit": type_produit,
            "numero_carte": numero_carte,
            "code_set": code_set,
            "nom_en": nom_en,
        }
    )


def compute_reference_median(
    prix_cm: Optional[float],
    prix_ebay_actif: Optional[float],
    prix_vinted: Optional[float],
    *,
    nb_ebay: int = 0,
    nb_vinted: int = 0,
) -> Optional[float]:
    """
    Médiane sur les sources disponibles.
    - eBay / Vinted : inclus seulement si >= 3 annonces
    - CardMarket : inclus si prix_actuel présent
    - Médiane None si < 2 sources valides
    """
    values: list[float] = []
    if prix_cm is not None and prix_cm > 0:
        values.append(float(prix_cm))
    if (
        prix_ebay_actif is not None
        and prix_ebay_actif > 0
        and nb_ebay >= MIN_LISTINGS_PER_SOURCE
    ):
        values.append(float(prix_ebay_actif))
    if (
        prix_vinted is not None
        and prix_vinted > 0
        and nb_vinted >= MIN_LISTINGS_PER_SOURCE
    ):
        values.append(float(prix_vinted))
    if len(values) < MIN_SOURCES_FOR_MEDIAN:
        return None
    return median_price(*values)


def deal_score_label(prix_achat: Optional[float], prix_reference: Optional[float]) -> dict[str, Any]:
    if prix_reference is None or prix_reference <= 0 or prix_achat is None:
        return {"emoji": "⚪", "label": "Pas de données", "cls": "score-none", "pct": None}
    ratio = float(prix_achat) / float(prix_reference)
    pct = round((1 - ratio) * 100, 1)
    if ratio < 0.80:
        return {"emoji": "🟢", "label": "Excellente affaire", "cls": "score-good", "pct": pct}
    if ratio < 0.90:
        return {"emoji": "🟡", "label": "Bonne affaire", "cls": "score-ok", "pct": pct}
    if ratio > 1.0:
        return {"emoji": "🔴", "label": "Prix élevé", "cls": "score-bad", "pct": pct}
    return {"emoji": "⚪", "label": "Neutre", "cls": "score-none", "pct": pct}
