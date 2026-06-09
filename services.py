"""Logique métier : scraping, historique, propagation, KPIs."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID

from database import get_supabase
from scraper import EbaySoldData, scrape_cardmarket_url, scrape_ebay_sold, scrape_multiple

logger = logging.getLogger(__name__)
log = logger  # alias diagnostic


def _diag(msg: str, *args: object) -> None:
    """Log + print stdout (Railway affiche toujours les prints)."""
    text = msg % args if args else msg
    log.info(text)
    print(text, flush=True)


def compute_urgence(prix_actuel: Optional[float], prix_cible: float) -> str:
    if prix_actuel is None or prix_cible <= 0:
        return "Inconnu"
    ratio = prix_actuel / prix_cible
    if ratio <= 0.95:
        return "Bonne affaire"
    if ratio <= 1.08:
        return "À surveiller"
    return "Trop cher"


def _first_price(pokedex_id: str) -> Optional[float]:
    sb = get_supabase()
    r = (
        sb.table("historique_prix")
        .select("prix")
        .eq("pokedex_id", pokedex_id)
        .order("date")
        .limit(1)
        .execute()
    )
    if r.data:
        return float(r.data[0]["prix"])
    return None


def append_historique(
    pokedex_id: str,
    prix: float,
    tendance_7j: Optional[float],
    old_price: Optional[float],
) -> None:
    sb = get_supabase()
    j0 = _first_price(pokedex_id)
    var_j1_eur = var_j1_pct = var_j0_eur = var_j0_pct = None
    if old_price is not None:
        var_j1_eur = float(prix - old_price)
        var_j1_pct = float((var_j1_eur / old_price) * 100) if old_price else None
    if j0 is not None and j0 != 0:
        var_j0_eur = float(prix - j0)
        var_j0_pct = float((var_j0_eur / j0) * 100)
    elif j0 is None:
        var_j0_eur = 0.0
        var_j0_pct = 0.0

    sb.table("historique_prix").insert(
        {
            "pokedex_id": pokedex_id,
            "prix": float(prix),
            "tendance_7j": float(tendance_7j) if tendance_7j is not None else None,
            "variation_j1_eur": var_j1_eur,
            "variation_j1_pct": var_j1_pct,
            "variation_j0_eur": var_j0_eur,
            "variation_j0_pct": var_j0_pct,
            "date": date.today().isoformat(),
        }
    ).execute()


def _ebay_update_fields(ebay: EbaySoldData) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if ebay.date_maj_ebay:
        fields["date_maj_ebay"] = ebay.date_maj_ebay.isoformat()
    if ebay.error and ebay.nb_ventes_ebay == 0 and ebay.prix_moyen_ebay is None:
        return fields
    fields["nb_ventes_ebay"] = ebay.nb_ventes_ebay
    if ebay.prix_moyen_ebay is not None:
        fields["prix_moyen_ebay"] = ebay.prix_moyen_ebay
        fields["prix_min_ebay"] = ebay.prix_min_ebay
        fields["prix_max_ebay"] = ebay.prix_max_ebay
    return fields


async def scrape_and_update_pokedex(pokedex_id: str, url: str, etat: str = "Near Mint") -> dict[str, Any]:
    _diag("DEBUT scraping pokedex_id=%s url=%s etat=%s", pokedex_id, url, etat)
    try:
        sb = get_supabase()
        _diag("Lecture Supabase id=%s", pokedex_id)
        row = sb.table("pokedex").select("*").eq("id", pokedex_id).single().execute()
        if not row.data:
            _diag("ERREUR carte introuvable: %s", pokedex_id)
            return {"success": False, "error": "Carte introuvable", "pokedex_id": pokedex_id}

        old_price = row.data.get("prix_actuel")
        row_nom = row.data.get("nom") or ""
        row_ext = row.data.get("extension") or ""
        row_langue = row.data.get("langue") or "FR"
        row_ebay_kw = row.data.get("ebay_keyword")
        row_ebay_url = row.data.get("ebay_url")
        _diag(
            "Carte en base: nom=%r extension=%r langue=%s ebay_url=%s",
            row_nom,
            row_ext,
            row_langue,
            bool(row_ebay_url),
        )

        _diag("Lancement Playwright (CardMarket)...")
        data = await scrape_cardmarket_url(url, etat, langue=row_langue)
        _diag(
            "Playwright termine: prix=%s nom=%r error=%r",
            data.prix_actuel,
            data.nom,
            data.error,
        )

        _diag("Lancement eBay...")
        ebay = await scrape_ebay_sold(
            row_nom,
            row_ext,
            langue=row_langue,
            ebay_keyword=row_ebay_kw,
            ebay_url=row_ebay_url,
        )
        _diag(
            "eBay termine: moy=%s nb=%s error=%r",
            ebay.prix_moyen_ebay,
            ebay.nb_ventes_ebay,
            ebay.error,
        )

        final_nom = (data.nom or row_nom).strip()
        final_ext = (data.extension or row_ext).strip()
        if final_nom and (final_nom != row_nom or final_ext != row_ext) and (
            ebay.nb_ventes_ebay == 0 or ebay.error
        ):
            _diag("Retry eBay nom=%r ext=%r", final_nom, final_ext)
            ebay = await scrape_ebay_sold(
                final_nom,
                final_ext,
                langue=row_langue,
                ebay_keyword=row_ebay_kw,
                ebay_url=row_ebay_url,
            )

        if data.error and not data.prix_actuel:
            _diag("CardMarket sans prix: %s", data.error)
            ebay_fields = _ebay_update_fields(ebay)
            if ebay_fields:
                sb.table("pokedex").update(ebay_fields).eq("id", pokedex_id).execute()
            return {"success": False, "error": data.error, "pokedex_id": pokedex_id}

        update = {
            "nom": final_nom or "Carte",
            "extension": final_ext,
            "prix_actuel": float(data.prix_actuel) if data.prix_actuel else old_price,
            "tendance_7j": float(data.tendance_7j) if data.tendance_7j is not None else None,
            "image_url": data.image_url,
            "derniere_maj": datetime.now(timezone.utc).isoformat(),
            **_ebay_update_fields(ebay),
        }
        _diag("Mise a jour Supabase id=%s", pokedex_id)
        if data.prix_actuel:
            sb.table("pokedex").update(update).eq("id", pokedex_id).execute()
            append_historique(pokedex_id, float(data.prix_actuel), data.tendance_7j, old_price)
        else:
            sb.table("pokedex").update(
                {k: v for k, v in update.items() if k != "prix_actuel"}
            ).eq("id", pokedex_id).execute()

        await propagate_radar_urgency()
        _diag("FIN scraping OK id=%s", pokedex_id)
        return {
            "success": True,
            "pokedex_id": pokedex_id,
            "nom": update["nom"],
            "prix_actuel": update.get("prix_actuel"),
            "prix_moyen_ebay": update.get("prix_moyen_ebay"),
            "image_url": update.get("image_url"),
        }
    except Exception as exc:
        log.error("ERREUR scraping id=%s url=%s: %s", pokedex_id, url, exc, exc_info=True)
        print(f"ERREUR scraping: {exc}", flush=True)
        raise


async def scrape_all_cards() -> dict[str, Any]:
    sb = get_supabase()
    cards = (
        sb.table("pokedex")
        .select(
            "id, nom, extension, url_cardmarket, etat, prix_actuel, "
            "langue, ebay_keyword, ebay_url"
        )
        .execute()
        .data
        or []
    )
    if not cards:
        return {"success": True, "scraped": 0, "errors": []}

    urls = [
        (c["url_cardmarket"], c.get("etat") or "Near Mint", c.get("langue") or "FR")
        for c in cards
    ]
    results = await scrape_multiple(urls)
    ok, errors = 0, []
    for card, data in zip(cards, results):
        nom = (data.nom or card.get("nom") or "").strip()
        ext = (data.extension or card.get("extension") or "").strip()
        ebay = await scrape_ebay_sold(
            nom,
            ext,
            langue=card.get("langue") or "FR",
            ebay_keyword=card.get("ebay_keyword"),
            ebay_url=card.get("ebay_url"),
        )
        if data.error and not data.prix_actuel:
            ebay_fields = _ebay_update_fields(ebay)
            if ebay_fields:
                sb.table("pokedex").update(ebay_fields).eq("id", card["id"]).execute()
            errors.append({"id": card["id"], "error": data.error})
            continue
        old = card.get("prix_actuel")
        upd = {
            "nom": nom or "Carte",
            "extension": ext,
            "prix_actuel": float(data.prix_actuel) if data.prix_actuel else old,
            "tendance_7j": float(data.tendance_7j) if data.tendance_7j is not None else None,
            "image_url": data.image_url,
            "derniere_maj": datetime.now(timezone.utc).isoformat(),
            **_ebay_update_fields(ebay),
        }
        sb.table("pokedex").update(upd).eq("id", card["id"]).execute()
        if data.prix_actuel:
            append_historique(card["id"], float(data.prix_actuel), data.tendance_7j, old)
        ok += 1

    await propagate_radar_urgency()
    return {"success": True, "scraped": ok, "total": len(cards), "errors": errors}


async def propagate_radar_urgency() -> None:
    """Recalcule l'urgence radar depuis les prix Pokédex."""
    sb = get_supabase()
    radar = sb.table("radar").select("id, pokedex_id, prix_cible").execute().data or []
    for r in radar:
        if not r.get("pokedex_id"):
            continue
        p = sb.table("pokedex").select("prix_actuel").eq("id", r["pokedex_id"]).single().execute()
        prix = p.data.get("prix_actuel") if p.data else None
        urgence = compute_urgence(
            float(prix) if prix is not None else None,
            float(r["prix_cible"]),
        )
        sb.table("radar").update({"urgence": urgence}).eq("id", r["id"]).execute()


def enrich_stock_row(row: dict) -> dict:
    if row.get("prix_achat") and row.get("prix_actuel"):
        row["marge_latente"] = float(row["prix_actuel"]) - float(row["prix_achat"])
    else:
        row["marge_latente"] = None
    return row


def get_dashboard_charts() -> dict[str, Any]:
    """Séries temporelles : valeur stock (historique) + CA par jour."""
    from collections import defaultdict

    sb = get_supabase()
    stock_rows = (
        sb.table("stock")
        .select("pokedex_id")
        .in_("statut", ["En stock", "En vente"])
        .execute()
        .data
        or []
    )
    pokedex_ids = {s["pokedex_id"] for s in stock_rows if s.get("pokedex_id")}

    ventes = sb.table("ventes").select("date_vente, prix_vente").execute().data or []
    ca_by_date: dict[str, float] = defaultdict(float)
    for v in ventes:
        d = str(v.get("date_vente") or "")[:10]
        if d:
            ca_by_date[d] += float(v.get("prix_vente") or 0)

    hist = (
        sb.table("historique_prix")
        .select("pokedex_id, prix, date")
        .order("date")
        .execute()
        .data
        or []
    )
    by_date: dict[str, dict[str, float]] = defaultdict(dict)
    for h in hist:
        pid = h.get("pokedex_id")
        if pid not in pokedex_ids:
            continue
        d = str(h.get("date") or "")[:10]
        if d:
            by_date[d][pid] = float(h.get("prix") or 0)

    all_dates = sorted(set(by_date.keys()) | set(ca_by_date.keys()))
    if not all_dates:
        today = date.today().isoformat()
        kpis = get_dashboard_kpis()
        return {
            "labels": [today],
            "valeur_stock": [kpis["valeur_stock"]],
            "chiffre_affaires": [ca_by_date.get(today, 0)],
        }

    last_prices: dict[str, float] = {}
    valeur_series: list[float] = []
    ca_series: list[float] = []
    for d in all_dates:
        last_prices.update(by_date.get(d, {}))
        val = sum(last_prices.get(pid, 0) for pid in pokedex_ids if pid in last_prices)
        valeur_series.append(round(val, 2))
        ca_series.append(round(ca_by_date.get(d, 0), 2))

    kpis = get_dashboard_kpis()
    if date.today().isoformat() not in all_dates:
        all_dates.append(date.today().isoformat())
        valeur_series.append(kpis["valeur_stock"])
        ca_series.append(ca_by_date.get(date.today().isoformat(), 0))

    return {
        "labels": all_dates,
        "valeur_stock": valeur_series,
        "chiffre_affaires": ca_series,
    }


def get_dashboard_kpis() -> dict[str, Any]:
    sb = get_supabase()
    stock = (
        sb.table("stock")
        .select("id, prix_achat, quantite, statut, pokedex_id")
        .execute()
        .data
        or []
    )
    ventes = sb.table("ventes").select("prix_vente, frais_plateforme, stock_id").execute().data or []
    pokedex = sb.table("pokedex").select("id, prix_actuel").execute().data or []
    prix_map = {p["id"]: p.get("prix_actuel") for p in pokedex}

    capital = sum(
        float(s["prix_achat"] or 0) * int(s.get("quantite") or 1)
        for s in stock
        if s.get("statut") in ("En stock", "En vente")
    )
    valeur = sum(
        float(prix_map.get(s["pokedex_id"]) or 0) * int(s.get("quantite") or 1)
        for s in stock
        if s.get("statut") in ("En stock", "En vente") and s.get("pokedex_id")
    )
    marge_latente = sum(
        (float(prix_map.get(s["pokedex_id"]) or 0) - float(s["prix_achat"] or 0))
        * int(s.get("quantite") or 1)
        for s in stock
        if s.get("statut") in ("En stock", "En vente")
        and s.get("pokedex_id")
        and s.get("prix_achat") is not None
        and prix_map.get(s["pokedex_id"]) is not None
    )
    ca = sum(float(v["prix_vente"] or 0) for v in ventes)
    frais = sum(float(v.get("frais_plateforme") or 0) for v in ventes)
    stock_by_id = {s["id"]: s for s in stock}
    cout_vendus = 0.0
    for v in ventes:
        st = stock_by_id.get(v.get("stock_id"))
        if st:
            cout_vendus += float(st.get("prix_achat") or 0) * int(st.get("quantite") or 1)
    benefice = ca - frais - cout_vendus
    marge_pct = ((benefice / cout_vendus) * 100) if cout_vendus else 0.0

    return {
        "capital_investi": round(capital, 2),
        "valeur_stock": round(valeur, 2),
        "marge_latente_totale": round(marge_latente, 2),
        "ca_total": round(ca, 2),
        "benefice_net": round(benefice, 2),
        "marge_moyenne_pct": round(marge_pct, 2),
        "nb_cartes_pokedex": len(pokedex),
        "nb_en_stock": sum(
            int(s.get("quantite") or 1)
            for s in stock
            if s.get("statut") == "En stock"
        ),
        "nb_radar": len(sb.table("radar").select("id").execute().data or []),
        "nb_vendus": sum(
            int(s.get("quantite") or 1)
            for s in stock
            if s.get("statut") == "Vendu"
        ),
    }
