"""Calculs prix de référence (médiane multi-sources) et scores d'opportunité."""

from __future__ import annotations

from typing import Any, Optional

from ebay import build_keywords, resolve_ebay_search


def median_price(*values: Optional[float]) -> Optional[float]:
    nums = sorted(v for v in values if v is not None and v > 0)
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return round(nums[mid], 2)
    return round((nums[mid - 1] + nums[mid]) / 2, 2)


def market_keyword_for_card(
    nom: str,
    extension: str = "",
    *,
    langue: str = "FR",
    ebay_keyword: Optional[str] = None,
    ebay_url: Optional[str] = None,
) -> str:
    """Mot-clé de recherche marché (eBay / Vinted) — même logique que eBay."""
    plan = resolve_ebay_search(
        nom,
        extension,
        langue=langue,
        ebay_keyword=ebay_keyword,
        ebay_url=ebay_url,
    )
    if plan.keywords:
        return plan.keywords
    return build_keywords(nom, extension, langue)


def compute_reference_median(
    prix_cm: Optional[float],
    prix_ebay_actif: Optional[float],
    prix_vinted: Optional[float],
) -> Optional[float]:
    return median_price(prix_cm, prix_ebay_actif, prix_vinted)


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
