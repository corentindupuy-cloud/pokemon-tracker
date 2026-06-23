"""Type produit, keywords marché (eBay / Vinted) et filtrage prix/titres."""

from __future__ import annotations

import re
from typing import Any, Optional

TYPES_PRODUIT = frozenset({"single", "etb", "display", "bundle", "collection", "promo"})
SEALED_TYPES = frozenset({"etb", "display", "bundle", "collection", "promo"})

MIN_PRICE_SINGLE_EUR = 5.0
MIN_PRICE_SEALED_EUR = 15.0

_STOP_WORDS = frozenset({
    "pokemon", "carte", "card", "the", "and", "for", "avec", "set", "sealed",
    "fr", "en", "jp", "nm", "mint", "near", "box", "booster", "pack", "french",
    "japanese", "english",
})


def _nom_court(nom: str) -> str:
    return (nom or "").split("|")[0].strip()


def build_keyword(card: dict[str, Any]) -> str:
    """Génère le mot-clé de recherche eBay / Vinted depuis la fiche Pokédex."""
    nom = _nom_court(card.get("nom", ""))
    langue = (card.get("langue") or "FR").upper()
    type_produit = (card.get("type_produit") or "single").lower()
    numero = (card.get("numero_carte") or "").strip()
    code_set = (card.get("code_set") or "").strip()
    nom_en = (card.get("nom_en") or "").strip()

    if type_produit == "single":
        parts = [nom] if nom else []
        if numero:
            parts.append(numero)
        if code_set:
            parts.append(code_set)
        if langue == "FR":
            parts.append("French")
        elif langue == "JP":
            parts.append("Japanese")
        return " ".join(p for p in parts if p)

    base = nom_en if nom_en else nom
    parts = [base, "sealed"] if base else ["sealed"]
    if langue == "FR":
        parts.append("French")
    return " ".join(parts)


def min_price_for_type(type_produit: str) -> float:
    t = (type_produit or "single").lower()
    return MIN_PRICE_SINGLE_EUR if t == "single" else MIN_PRICE_SEALED_EUR


def is_sealed_type(type_produit: str) -> bool:
    return (type_produit or "single").lower() in SEALED_TYPES


def title_tokens_for_card(card: dict[str, Any]) -> list[str]:
    """Mots du nom utilisés pour filtrer les titres d'annonces."""
    nom = _nom_court(card.get("nom", ""))
    nom_en = (card.get("nom_en") or "").strip()
    numero = (card.get("numero_carte") or "").strip()
    code_set = (card.get("code_set") or "").strip()
    raw = f"{nom} {nom_en} {numero} {code_set}"
    words = re.findall(r"[a-zàâäéèêëïîôùûüç0-9/]{2,}", raw.lower(), re.I)
    tokens: list[str] = []
    for w in words:
        if w in _STOP_WORDS:
            continue
        if "/" in w:
            tokens.extend(p for p in w.split("/") if len(p) >= 2)
            tokens.append(w.replace("/", ""))
        elif len(w) >= 3 or w.isdigit():
            tokens.append(w)
    return list(dict.fromkeys(tokens))


def title_matches(titre: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    t = titre.lower()
    return any(tok in t for tok in tokens)


def resolve_market_keyword(card: dict[str, Any]) -> str:
    """Priorité : keyword manuel → extraction URL eBay → build_keyword."""
    manual = (card.get("ebay_keyword") or "").strip()
    if manual:
        return manual

    ebay_url = (card.get("ebay_url") or "").strip()
    if ebay_url:
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(ebay_url).query)
        for key in ("_nkw", "q", "_skw"):
            if qs.get(key):
                kw = qs[key][0].replace("+", " ").strip()
                if kw:
                    return kw

    return build_keyword(card)
