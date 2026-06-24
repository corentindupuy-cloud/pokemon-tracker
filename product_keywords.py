"""Type produit, keywords marché (eBay / Vinted) et filtrage prix/titres."""

from __future__ import annotations

import re
from typing import Any, Optional

TYPES_PRODUIT = frozenset({"single", "etb", "display", "bundle", "collection", "promo"})
SEALED_TYPES = frozenset({"etb", "display", "bundle", "collection", "promo"})

# Prix minimum (€) par type de produit (filtre annonces aberrantes)
MIN_PRICE_BY_TYPE = {
    "single": 5.0,
    "etb": 30.0,
    "display": 50.0,
    "collection": 50.0,
    "bundle": 30.0,
    "promo": 5.0,
}
MIN_PRICE_DEFAULT = 15.0

# Libellé produit ajouté au keyword pour les scellés
_SEALED_LABEL = {
    "etb": "Elite Trainer Box",
    "display": "Booster Box",
    "collection": "Premium Collection",
    "bundle": "Booster Bundle",
    "promo": "Promo",
}

# Langue → terme anglais pour le keyword single
_LANGUE_EN = {
    "FR": "French",
    "JP": "Japanese",
    "IT": "Italian",
    "DE": "German",
    "ES": "Spanish",
    "EN": "",
}

# Détection du type depuis l'URL CardMarket (segment → type)
_URL_TYPE_PATTERNS = [
    ("single", ("/singles/",)),
    ("etb", ("/elite-trainer-box", "/elite-trainer-boxes")),
    ("display", ("/booster-boxes", "/booster-box")),
    ("collection", ("/box-sets", "/box-set")),
    ("bundle", ("/booster-bundle", "/booster-bundles")),
]

# Suffixes produit à retirer du dernier segment d'URL pour isoler le nom de set
_SET_NAME_SUFFIXES = (
    "elite trainer box",
    "booster box",
    "booster bundle",
    "premium collection",
    "box set",
    "box sets",
    "collection",
    "display",
)

_STOP_WORDS = frozenset({
    "pokemon", "carte", "card", "the", "and", "for", "avec", "set", "sealed",
    "fr", "en", "jp", "nm", "mint", "near", "box", "booster", "pack", "french",
    "japanese", "english",
})


def _nom_court(nom: str) -> str:
    return (nom or "").split("|")[0].strip()


def detect_type_from_url(url: str) -> Optional[str]:
    """Détecte le type de produit depuis l'URL CardMarket."""
    if not url:
        return None
    low = url.lower()
    for type_produit, needles in _URL_TYPE_PATTERNS:
        if any(n in low for n in needles):
            return type_produit
    return None


def extract_set_name_from_url(url: str) -> str:
    """
    Extrait le nom du set depuis le dernier segment de l'URL CardMarket.

    /Elite-Trainer-Boxes/Chaos-Rising-Elite-Trainer-Box → "Chaos Rising"
    """
    if not url:
        return ""
    path = url.split("?")[0].rstrip("/")
    segment = path.rsplit("/", 1)[-1] if "/" in path else path
    name = segment.replace("-", " ").replace("_", " ").strip()
    low = name.lower()
    for suffix in sorted(_SET_NAME_SUFFIXES, key=len, reverse=True):
        if low.endswith(suffix):
            name = name[: len(name) - len(suffix)].strip()
            break
    return name


def build_keyword(card: dict[str, Any]) -> str:
    """Génère le mot-clé de recherche eBay / Vinted depuis la fiche Pokédex."""
    url = card.get("url_cardmarket", "")
    nom = _nom_court(card.get("nom", ""))
    langue = (card.get("langue") or "FR").upper()
    type_produit = (card.get("type_produit") or detect_type_from_url(url) or "single").lower()
    numero = (card.get("numero_carte") or "").strip()
    code_set = (card.get("code_set") or "").strip()
    nom_en = (card.get("nom_en") or "").strip()

    if type_produit == "single":
        langue_en = _LANGUE_EN.get(langue, "")
        parts = [nom, numero, code_set, "Pokemon", langue_en]
        return " ".join(p for p in parts if p)

    nom_set = nom_en or extract_set_name_from_url(url) or nom
    label = _SEALED_LABEL.get(type_produit, "")
    parts = [nom_set, label, "Pokemon", "sealed"]
    return " ".join(p for p in parts if p)


def min_price_for_type(type_produit: str) -> float:
    t = (type_produit or "single").lower()
    return MIN_PRICE_BY_TYPE.get(t, MIN_PRICE_DEFAULT)


def is_sealed_type(type_produit: str) -> bool:
    return (type_produit or "single").lower() in SEALED_TYPES


def title_tokens_for_card(card: dict[str, Any]) -> list[str]:
    """Mots du nom utilisés pour filtrer les titres d'annonces."""
    url = card.get("url_cardmarket") or ""
    type_produit = (
        card.get("type_produit")
        or detect_type_from_url(url)
        or "single"
    ).lower()

    if type_produit != "single":
        set_name = (card.get("nom_en") or "").strip() or extract_set_name_from_url(url)
        raw = set_name or _nom_court(card.get("nom", ""))
        words = re.findall(r"[a-zàâäéèêëïîôùûüç0-9]{3,}", raw.lower(), re.I)
        return [w for w in words if w not in _STOP_WORDS]

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
