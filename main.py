"""
Pokémon Tracker — API FastAPI + interface web.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from database import get_supabase
from models import (
    LANGUES_POKEDEX,
    TYPES_PRODUIT,
    DashboardFullOut,
    DashboardKPIs,
    PokedexCreate,
    PokedexOut,
    DashboardCharts,
    EbayActiveResponse,
    MarketSyncResult,
    SearchResultOut,
    RadarCreate,
    RadarOut,
    RadarUpdate,
    ScrapeResultOut,
    StockCreate,
    StockOut,
    StockUpdate,
    VenteCreate,
    VenteOut,
    VintedActiveResponse,
)
from services import (
    enrich_stock_row,
    get_dashboard_charts,
    get_dashboard_extras,
    get_dashboard_kpis,
    propagate_radar_urgency,
    scrape_all_cards,
    scrape_and_update_pokedex,
)
from scraper import scrape_cardmarket_url
from ebay import fetch_sold_items, get_cached_sales, sync_pokedex_sales
from ebay_browse import fetch_active_listings, stats_from_active_listings
from market_sync import sync_all_market_prices
from product_keywords import detect_type_from_url, extract_set_name_from_url
from vinted_api import fetch_vinted_listings

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env", override=True)


def _configure_logging() -> None:
    """Logs visibles sur Railway (stdout)."""
    import sys

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in ("services", "scraper", "ebay", "vinted_api", "market_sync", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.INFO)


_configure_logging()
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
scheduler = AsyncIOScheduler()


def _static_asset_version() -> str:
    """Version cache-bust basée sur la date de modif des assets (évite JS/CSS obsolètes)."""
    mtimes: list[float] = []
    for name in ("app.js", "style.css", "index.html", "images/hajime-logo.png"):
        path = STATIC_DIR / name
        if path.is_file():
            mtimes.append(path.stat().st_mtime)
    return str(int(max(mtimes))) if mtimes else "1"


async def scheduled_scrape_job() -> None:
    logger.info("Scraping planifié 8h00")
    try:
        result = await scrape_all_cards()
        logger.info("Scraping terminé: %s", result)
    except Exception as exc:
        logger.exception("Erreur scraping planifié: %s", exc)


async def scheduled_ebay_sync_job() -> None:
    logger.info("Sync eBay planifié 9h00")
    try:
        sb = get_supabase()
        cards = (
            sb.table("pokedex")
            .select("id, nom, extension, langue, ebay_keyword, ebay_url")
            .order("nom")
            .execute()
            .data
            or []
        )
        ok, err = 0, 0
        for c in cards:
            try:
                await sync_pokedex_sales(
                    str(c["id"]),
                    c.get("nom") or "",
                    c.get("extension") or "",
                    langue=c.get("langue") or "FR",
                    ebay_keyword=c.get("ebay_keyword"),
                    ebay_url=c.get("ebay_url"),
                )
                ok += 1
            except Exception as exc:  # noqa: BLE001
                err += 1
                logger.warning("Sync eBay %s: %s", c.get("nom") or c["id"], exc)
        logger.info("Sync eBay terminé: %s OK / %s erreurs", ok, err)
    except Exception as exc:
        logger.exception("Erreur sync eBay planifié: %s", exc)


async def scheduled_trending_snapshot_job() -> None:
    """Snapshot hebdo des tendances (lundi 7h)."""
    logger.info("Snapshot trending planifié (lundi 7h)")
    try:
        from ebay import fetch_sold_items  # local import

        sb = get_supabase()
        today = date.today().isoformat()
        categories = [("183454", "Pokemon Trading Cards"), ("214", "NBA Memorabilia")]
        rows: list[dict] = []
        for cat_id, cat_name in categories:
            try:
                sales = await fetch_sold_items(keywords=None, category_id=cat_id, days=7)
            except Exception as exc:
                logger.warning("Trending snapshot %s: %s", cat_id, exc)
                continue
            by_title: dict[str, list[float]] = {}
            url_by_title: dict[str, str] = {}
            for s in sales:
                by_title.setdefault(s.titre, [])
                if s.prix_vente:
                    by_title[s.titre].append(float(s.prix_vente))
                if s.url_ebay and s.titre not in url_by_title:
                    url_by_title[s.titre] = s.url_ebay
            for title, prices in by_title.items():
                if not prices:
                    continue
                avg = round(sum(prices) / len(prices), 2)
                rows.append(
                    {
                        "titre": title,
                        "categorie": cat_name,
                        "nb_ventes_7j": len(prices),
                        "prix_moyen": avg,
                        "url_ebay": url_by_title.get(title),
                        "date_snapshot": today,
                    }
                )

        # variation vs semaine précédente
        prev = (
            sb.table("trending_items")
            .select("titre, categorie, prix_moyen, date_snapshot")
            .order("date_snapshot", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )
        prev_map: dict[tuple[str, str], float] = {}
        for p in prev:
            key = (p.get("titre"), p.get("categorie"))
            if key[0] and key[1] and key not in prev_map and p.get("prix_moyen"):
                prev_map[key] = float(p["prix_moyen"])

        for r in rows:
            key = (r["titre"], r["categorie"])
            old = prev_map.get(key)
            if old and old != 0:
                r["variation_prix_pct"] = round(((r["prix_moyen"] - old) / old) * 100, 2)
            else:
                r["variation_prix_pct"] = None

        rows.sort(key=lambda x: x.get("nb_ventes_7j") or 0, reverse=True)
        rows = rows[:200]
        if rows:
            sb.table("trending_items").insert(rows).execute()
        logger.info("Snapshot trending: %s items", len(rows))
    except Exception as exc:
        logger.exception("Erreur snapshot trending: %s", exc)


async def scheduled_market_sync_job() -> None:
    """eBay actif + Vinted + médiane pour chaque carte (après CM local)."""
    logger.info("Sync marché planifié (eBay actif + Vinted)")
    try:
        result = await sync_all_market_prices()
        logger.info("Sync marché terminé: %s", result)
    except Exception as exc:
        logger.exception("Erreur sync marché planifié: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(scheduled_scrape_job, "cron", hour=8, minute=0, id="daily_scrape")
    scheduler.add_job(scheduled_ebay_sync_job, "cron", hour=9, minute=0, id="daily_ebay_sync")
    scheduler.add_job(scheduled_market_sync_job, "cron", hour=8, minute=30, id="daily_market_sync")
    scheduler.add_job(scheduled_trending_snapshot_job, "cron", day_of_week="mon", hour=7, minute=0, id="weekly_trending")
    scheduler.start()
    logger.info("Scheduler démarré (scraping quotidien 8h00)")
    yield
    scheduler.shutdown()


app = FastAPI(title="Pokémon Tracker", lifespan=lifespan)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        html = html.replace("{{STATIC_VERSION}}", _static_asset_version())
        return HTMLResponse(
            html,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    return HTMLResponse("<h1>Hajime</h1><p>static/index.html manquant</p>")


def _proxy_headers_for_url(url: str) -> dict[str, str]:
    base = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }
    if "product-images" in url or "static.cardmarket" in url:
        base["Referer"] = "https://www.cardmarket.com/"
        base["Origin"] = "https://www.cardmarket.com"
    else:
        base["Referer"] = "https://www.cardmarket.com/fr/Pokemon"
    return base


def _normalize_image_content_type(url: str, raw_type: Optional[str], content: bytes) -> str:
    if raw_type:
        ct = raw_type.split(";")[0].strip().lower()
        if ct.startswith("image/") and "auto_content" not in ct:
            return ct
    url_lower = url.lower()
    if url_lower.endswith(".png"):
        return "image/png"
    if url_lower.endswith(".webp"):
        return "image/webp"
    if url_lower.endswith(".gif"):
        return "image/gif"
    if len(content) >= 3 and content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(content) >= 8 and content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


@app.get("/api/image-proxy")
async def image_proxy(url: str = Query(..., description="URL image CardMarket")):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL invalide")
    if "cardmarket.com" not in url and "product-images" not in url:
        raise HTTPException(400, "Domaine non autorisé")

    headers = _proxy_headers_for_url(url)
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        alt = {**headers, "Referer": "https://www.cardmarket.com/fr/Pokemon/Products/"}
        for attempt_headers in (headers, alt):
            try:
                response = await client.get(url, headers=attempt_headers)
                response.raise_for_status()
                content = response.content
                if not content:
                    errors.append("Réponse vide")
                    continue
                media_type = _normalize_image_content_type(
                    url,
                    response.headers.get("content-type"),
                    content,
                )
                if "text/html" in media_type:
                    errors.append("HTML reçu au lieu d'une image")
                    continue
                return Response(
                    content=content,
                    media_type=media_type,
                    headers={"Cache-Control": "public, max-age=86400"},
                )
            except Exception as exc:
                errors.append(str(exc))

    logger.warning("Image proxy échec %s: %s", url[:100], errors)
    raise HTTPException(502, f"Image inaccessible: {errors[-1] if errors else 'inconnu'}")


# ─── Pokédex ───────────────────────────────────────────────────────────────

@app.get("/api/pokedex", response_model=list[PokedexOut])
async def list_pokedex():
    sb = get_supabase()
    data = sb.table("pokedex").select("*").order("nom").execute().data or []
    return data


@app.post("/api/pokedex", response_model=ScrapeResultOut)
async def add_pokedex(body: PokedexCreate):
    url = body.url_cardmarket.strip()
    if "cardmarket.com" not in url:
        raise HTTPException(400, "URL CardMarket requise")

    sb = get_supabase()
    existing = sb.table("pokedex").select("id").eq("url_cardmarket", url.split("?")[0]).execute()
    if existing.data:
        raise HTTPException(409, "Cette carte existe déjà")

    langue = (body.langue or "FR").upper()
    if langue not in LANGUES_POKEDEX:
        raise HTTPException(400, f"Langue invalide (attendu: {', '.join(sorted(LANGUES_POKEDEX))})")

    type_produit = (body.type_produit or "single").lower()
    if type_produit not in TYPES_PRODUIT:
        raise HTTPException(
            400,
            f"Type produit invalide (attendu: {', '.join(sorted(TYPES_PRODUIT))})",
        )

    ebay_kw = (body.ebay_keyword or "").strip() or None
    ebay_url = (body.ebay_url or "").strip() or None
    if ebay_url and "ebay." not in ebay_url.lower():
        raise HTTPException(400, "URL eBay invalide")

    numero = (body.numero_carte or "").strip() or None
    code_set = (body.code_set or "").strip() or None
    nom_en = (body.nom_en or "").strip() or None

    ins = sb.table("pokedex").insert(
        {
            "url_cardmarket": url.split("?")[0],
            "nom": "Chargement…",
            "etat": "Near Mint",
            "langue": langue,
            "type_produit": type_produit,
            "numero_carte": numero,
            "code_set": code_set,
            "nom_en": nom_en,
            "ebay_keyword": ebay_kw,
            "ebay_url": ebay_url,
        }
    ).execute()
    card = ins.data[0]
    pid = card["id"]
    logger.info("[API] POST /api/pokedex (nouvelle carte) id=%s", pid)

    result = await scrape_and_update_pokedex(pid, url)
    if not result.get("success"):
        return ScrapeResultOut(success=False, pokedex_id=pid, error=result.get("error"))
    return ScrapeResultOut(
        success=True,
        pokedex_id=pid,
        nom=result.get("nom"),
        prix_actuel=result.get("prix_actuel"),
        prix_moyen_ebay=result.get("prix_moyen_ebay"),
        image_url=result.get("image_url"),
    )


@app.post("/api/pokedex/{card_id}/scrape", response_model=ScrapeResultOut)
async def scrape_one(card_id: UUID):
    print(f"[API] POST /api/pokedex/{card_id}/scrape", flush=True)
    logger.info("[API] POST /api/pokedex/%s/scrape", card_id)
    sb = get_supabase()
    row = sb.table("pokedex").select("*").eq("id", str(card_id)).single().execute()
    if not row.data:
        raise HTTPException(404, "Carte introuvable")
    result = await scrape_and_update_pokedex(
        str(card_id), row.data["url_cardmarket"], row.data.get("etat") or "Near Mint"
    )
    if not result.get("success"):
        return ScrapeResultOut(success=False, pokedex_id=card_id, error=result.get("error"))
    return ScrapeResultOut(
        success=True,
        pokedex_id=card_id,
        nom=result.get("nom"),
        prix_actuel=result.get("prix_actuel"),
        prix_moyen_ebay=result.get("prix_moyen_ebay"),
        image_url=result.get("image_url"),
    )


@app.delete("/api/pokedex/{card_id}")
async def delete_pokedex(card_id: UUID):
    sb = get_supabase()
    res = sb.table("pokedex").delete().eq("id", str(card_id)).execute()
    if not res.data:
        raise HTTPException(404, "Carte Pokédex introuvable")
    return {"success": True}


@app.post("/api/scrape/all")
async def scrape_all():
    return await scrape_all_cards()


@app.get("/api/search", response_model=list[SearchResultOut])
async def search_cards(q: str = Query(..., min_length=2)):
    """Recherche CardMarket par nom (10 premiers résultats)."""
    from search import search_cardmarket

    return await search_cardmarket(q, limit=10)


# ─── Stock ─────────────────────────────────────────────────────────────────

@app.get("/api/stock")
async def list_stock(statut: Optional[str] = None):
    sb = get_supabase()
    q = sb.table("stock").select(
        "*, pokedex(nom, extension, image_url, prix_actuel, langue, "
        "prix_moyen_ebay, nb_ventes_ebay, ebay_url, prix_actif_ebay, "
        "nb_annonces_ebay_actif, prix_moyen_vinted, nb_annonces_vinted, "
        "prix_reference_mediane, tendance_7j, type_produit, numero_carte, code_set, nom_en)"
    )
    if statut:
        q = q.eq("statut", statut)
    rows = q.order("created_at", desc=True).execute().data or []
    out = []
    for r in rows:
        p = r.pop("pokedex", None) or {}
        row = {
            **r,
            "nom": p.get("nom"),
            "extension": p.get("extension"),
            "image_url": p.get("image_url"),
            "prix_actuel": p.get("prix_actuel"),
            "langue": p.get("langue"),
            "prix_moyen_ebay": p.get("prix_moyen_ebay"),
            "nb_ventes_ebay": p.get("nb_ventes_ebay"),
            "ebay_url": p.get("ebay_url"),
            "prix_actif_ebay": p.get("prix_actif_ebay"),
            "nb_annonces_ebay_actif": p.get("nb_annonces_ebay_actif"),
            "prix_moyen_vinted": p.get("prix_moyen_vinted"),
            "nb_annonces_vinted": p.get("nb_annonces_vinted"),
            "prix_reference_mediane": p.get("prix_reference_mediane"),
            "tendance_7j": p.get("tendance_7j"),
            "type_produit": p.get("type_produit") or "single",
            "numero_carte": p.get("numero_carte"),
            "code_set": p.get("code_set"),
            "nom_en": p.get("nom_en"),
        }
        out.append(enrich_stock_row(row))
    return out


@app.post("/api/stock", response_model=StockOut)
async def add_stock(body: StockCreate):
    sb = get_supabase()
    pid = str(body.pokedex_id)
    check = sb.table("pokedex").select("id").eq("id", pid).execute()
    if not check.data:
        raise HTTPException(404, "Carte Pokédex introuvable — ajoutez-la d'abord dans Pokédex")

    data = body.model_dump(exclude_none=True)
    data["pokedex_id"] = pid
    if data.get("date_achat"):
        data["date_achat"] = data["date_achat"].isoformat()
    try:
        ins = sb.table("stock").insert(data).execute()
    except Exception as exc:
        logger.exception("Insert stock: %s", exc)
        raise HTTPException(500, f"Erreur Supabase stock: {exc}") from exc

    if not ins.data:
        raise HTTPException(500, "Insertion stock échouée")
    row = ins.data[0]
    p = (
        sb.table("pokedex")
        .select("nom, extension, image_url, prix_actuel")
        .eq("id", row["pokedex_id"])
        .execute()
    )
    merged = {**row, **(p.data[0] if p.data else {})}
    return enrich_stock_row(merged)


@app.delete("/api/stock/{item_id}")
async def delete_stock(item_id: UUID):
    sb = get_supabase()
    res = sb.table("stock").delete().eq("id", str(item_id)).execute()
    if not res.data:
        raise HTTPException(404, "Ligne stock introuvable")
    return {"success": True}


@app.put("/api/stock/{item_id}")
async def update_stock(item_id: UUID, body: StockUpdate):
    sb = get_supabase()
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if not upd:
        raise HTTPException(400, "Rien à mettre à jour")
    sb.table("stock").update(upd).eq("id", str(item_id)).execute()
    return {"success": True}


# ─── Radar ─────────────────────────────────────────────────────────────────

@app.get("/api/radar", response_model=list[RadarOut])
async def list_radar():
    sb = get_supabase()
    rows = (
        sb.table("radar")
        .select(
            "*, pokedex(nom, extension, image_url, prix_actuel, langue, "
            "prix_moyen_ebay, nb_ventes_ebay, prix_actif_ebay, nb_annonces_ebay_actif, "
            "prix_moyen_vinted, nb_annonces_vinted, prix_reference_mediane, tendance_7j, "
            "type_produit, numero_carte, code_set, nom_en)"
        )
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    rows.sort(key=lambda r: (r.get("priorite") is None, -(r.get("priorite") or 0)))
    out = []
    for r in rows:
        p = r.pop("pokedex", None) or {}
        out.append({
            **r,
            "nom": p.get("nom"),
            "extension": p.get("extension"),
            "image_url": p.get("image_url"),
            "prix_actuel": p.get("prix_actuel"),
            "langue": p.get("langue"),
            "prix_moyen_ebay": p.get("prix_moyen_ebay"),
            "nb_ventes_ebay": p.get("nb_ventes_ebay"),
            "prix_actif_ebay": p.get("prix_actif_ebay"),
            "nb_annonces_ebay_actif": p.get("nb_annonces_ebay_actif"),
            "prix_moyen_vinted": p.get("prix_moyen_vinted"),
            "nb_annonces_vinted": p.get("nb_annonces_vinted"),
            "prix_reference_mediane": p.get("prix_reference_mediane"),
            "tendance_7j": p.get("tendance_7j"),
            "type_produit": p.get("type_produit") or "single",
            "numero_carte": p.get("numero_carte"),
            "code_set": p.get("code_set"),
            "nom_en": p.get("nom_en"),
        })
    return out


@app.post("/api/radar", response_model=RadarOut)
async def add_radar(body: RadarCreate):
    sb = get_supabase()
    pid = str(body.pokedex_id)
    check = sb.table("pokedex").select("id").eq("id", pid).execute()
    if not check.data:
        raise HTTPException(404, "Carte Pokédex introuvable")

    data = body.model_dump(exclude_none=True)
    data["pokedex_id"] = pid
    p = sb.table("pokedex").select("prix_actuel, prix_reference_mediane").eq("id", pid).execute()
    from services import compute_urgence

    prix_row = p.data[0] if p.data else {}
    prix_actuel = prix_row.get("prix_reference_mediane") or prix_row.get("prix_actuel")
    data["urgence"] = compute_urgence(
        float(prix_actuel) if prix_actuel is not None else None,
        float(data["prix_cible"]),
    )
    try:
        ins = sb.table("radar").insert(data).execute()
    except Exception as exc:
        logger.exception("Insert radar: %s", exc)
        raise HTTPException(500, f"Erreur Supabase radar: {exc}") from exc

    if not ins.data:
        raise HTTPException(500, "Insertion radar échouée")
    row = ins.data[0]
    pk = (
        sb.table("pokedex")
        .select("nom, extension, image_url, prix_actuel")
        .eq("id", row["pokedex_id"])
        .execute()
    )
    return {**row, **(pk.data[0] if pk.data else {})}


@app.put("/api/radar/{item_id}")
async def update_radar(item_id: UUID, body: RadarUpdate):
    sb = get_supabase()
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if not upd:
        raise HTTPException(400, "Rien à mettre à jour")
    if "prix_cible" in upd:
        row = sb.table("radar").select("pokedex_id").eq("id", str(item_id)).execute()
        if row.data:
            from services import compute_urgence
            p = sb.table("pokedex").select("prix_actuel").eq("id", row.data[0]["pokedex_id"]).execute()
            prix = p.data[0].get("prix_actuel") if p.data else None
            upd["urgence"] = compute_urgence(
                float(prix) if prix is not None else None, float(upd["prix_cible"])
            )
    sb.table("radar").update(upd).eq("id", str(item_id)).execute()
    return {"success": True}


@app.delete("/api/radar/{item_id}")
async def delete_radar(item_id: UUID):
    sb = get_supabase()
    res = sb.table("radar").delete().eq("id", str(item_id)).execute()
    if not res.data:
        raise HTTPException(404, "Ligne radar introuvable")
    return {"success": True}


# ─── Ventes / Dashboard / Historique ───────────────────────────────────────

@app.get("/api/stock/for-vente")
async def stock_for_vente():
    """Lignes stock disponibles pour enregistrer une vente (dropdown)."""
    sb = get_supabase()
    rows = (
        sb.table("stock")
        .select("id, ref, prix_achat, quantite, statut, pokedex(nom, extension)")
        .in_("statut", ["En stock", "En vente"])
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    out = []
    for r in rows:
        p = r.get("pokedex") or {}
        label = f"{p.get('nom', '?')}"
        if r.get("ref"):
            label += f" ({r['ref']})"
        if p.get("extension"):
            label += f" — {p['extension']}"
        qty = int(r.get("quantite") or 1)
        if qty > 1:
            label += f" ×{qty}"
        out.append(
            {
                "id": r["id"],
                "label": label,
                "prix_achat": r.get("prix_achat"),
                "quantite": qty,
                "statut": r.get("statut"),
            }
        )
    return out


@app.post("/api/ventes")
async def add_vente(body: VenteCreate):
    sb = get_supabase()
    sid = str(body.stock_id)
    st = sb.table("stock").select("id, statut").eq("id", sid).execute()
    if not st.data:
        raise HTTPException(404, "Ligne stock introuvable")

    data = body.model_dump(exclude_none=True)
    data["stock_id"] = sid
    if data.get("date_vente"):
        data["date_vente"] = data["date_vente"].isoformat()
    else:
        data["date_vente"] = date.today().isoformat()

    try:
        ins = sb.table("ventes").insert(data).execute()
        sb.table("stock").update({"statut": "Vendu"}).eq("id", sid).execute()
    except Exception as exc:
        logger.exception("Insert vente: %s", exc)
        raise HTTPException(500, f"Erreur Supabase ventes: {exc}") from exc

    return {"success": True, "id": ins.data[0]["id"] if ins.data else None}


@app.get("/api/ventes")
async def list_ventes():
    sb = get_supabase()
    rows = (
        sb.table("ventes")
        .select("*, stock(ref, prix_achat, quantite, pokedex(nom))")
        .order("date_vente", desc=True)
        .execute()
        .data
        or []
    )
    out = []
    for v in rows:
        st = v.pop("stock", None) or {}
        p = st.pop("pokedex", None) or {}
        pa = float(st.get("prix_achat") or 0) * int(st.get("quantite") or 1)
        pv = float(v.get("prix_vente") or 0)
        frais = float(v.get("frais_plateforme") or 0)
        out.append({
            **v,
            "nom": p.get("nom"),
            "ref": st.get("ref"),
            "prix_achat": pa,
            "benefice": round(pv - frais - pa, 2),
        })
    return out


@app.get("/api/dashboard", response_model=DashboardFullOut)
async def dashboard():
    kpis = get_dashboard_kpis()
    extras = get_dashboard_extras()
    return {**kpis, **extras}


@app.get("/api/dashboard/charts", response_model=DashboardCharts)
async def dashboard_charts():
    try:
        return get_dashboard_charts()
    except Exception as exc:
        logger.exception("Dashboard charts: %s", exc)
        today = date.today().isoformat()
        kpis = get_dashboard_kpis()
        return {
            "labels": [today],
            "valeur_stock": [kpis.get("valeur_stock", 0)],
            "valeur_stock_cm": [kpis.get("valeur_stock_cm", 0)],
            "valeur_stock_ebay": [kpis.get("valeur_stock_ebay", 0)],
            "valeur_stock_mediane": [kpis.get("valeur_stock", 0)],
            "chiffre_affaires": [0],
        }


# ─── Admin ─────────────────────────────────────────────────────────────────

@app.get("/api/admin/fix-products")
async def fix_products():
    """
    Détecte le type_produit de chaque carte depuis son URL CardMarket
    et met à jour la base. Idempotent.
    """
    sb = get_supabase()
    cards = (
        sb.table("pokedex")
        .select("id, nom, url_cardmarket, type_produit")
        .execute()
        .data
        or []
    )
    updates: list[dict] = []
    unchanged = 0
    undetected = 0
    for c in cards:
        url = c.get("url_cardmarket") or ""
        detected = detect_type_from_url(url)
        if detected is None:
            undetected += 1
            continue
        if (c.get("type_produit") or "") == detected:
            unchanged += 1
            continue
        patch: dict[str, Any] = {"type_produit": detected}
        if detected != "single":
            set_name = extract_set_name_from_url(url)
            if set_name and not (c.get("nom_en") or "").strip():
                patch["nom_en"] = set_name
        sb.table("pokedex").update(patch).eq("id", c["id"]).execute()
        updates.append(
            {
                "id": c["id"],
                "nom": c.get("nom"),
                "type_produit": detected,
                "nom_en": patch.get("nom_en"),
            }
        )

    logger.info(
        "[admin] fix-products: %s mis à jour, %s inchangés, %s non détectés",
        len(updates),
        unchanged,
        undetected,
    )
    return {
        "success": True,
        "total": len(cards),
        "updated": len(updates),
        "unchanged": unchanged,
        "undetected": undetected,
        "details": updates,
    }


# ─── Sync marché (eBay actif + Vinted) ─────────────────────────────────────

@app.post("/api/sync/trigger", response_model=MarketSyncResult)
async def sync_trigger(x_sync_secret: Optional[str] = Header(None, alias="X-Sync-Secret")):
    """
    Déclenché par update_cotes.py (Mac) après scraping CardMarket.
    Lance eBay Browse + Vinted + calcul médiane pour chaque carte.
    """
    expected = (os.getenv("SYNC_API_SECRET") or "").strip()
    if expected and x_sync_secret != expected:
        raise HTTPException(403, "Secret sync invalide")
    try:
        result = await sync_all_market_prices()
        return result
    except Exception as exc:
        logger.exception("Sync marché: %s", exc)
        raise HTTPException(500, str(exc)) from exc


# ─── Vinted ────────────────────────────────────────────────────────────────

@app.get("/api/vinted/active", response_model=VintedActiveResponse)
async def vinted_active(
    q: str = Query(..., min_length=2, description="Mot-clé recherche Vinted"),
    langue: str = Query("FR", description="Langue / domaine Vinted"),
    type_produit: str = Query("single", description="Type produit"),
    nom: str = Query("", description="Nom carte (filtrage titre)"),
    extension: str = Query("", description="Extension"),
    numero_carte: str = Query("", description="Numéro carte"),
    code_set: str = Query("", description="Code set"),
    nom_en: str = Query("", description="Nom anglais"),
):
    keyword = q.strip()
    lang = (langue or "FR").upper()
    if lang not in LANGUES_POKEDEX:
        raise HTTPException(400, f"Langue invalide (attendu: {', '.join(sorted(LANGUES_POKEDEX))})")
    tp = (type_produit or "single").lower()
    if tp not in TYPES_PRODUIT:
        raise HTTPException(400, f"Type produit invalide")
    card = {
        "nom": nom.strip() or keyword,
        "extension": extension.strip(),
        "langue": lang,
        "type_produit": tp,
        "numero_carte": numero_carte.strip() or None,
        "code_set": code_set.strip() or None,
        "nom_en": nom_en.strip() or None,
    }
    try:
        data = await fetch_vinted_listings(keyword, langue=lang, card=card)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc

    return {
        "keyword": keyword,
        "langue": lang,
        "stats": {
            "prix_moyen_vinted": data.get("prix_moyen_vinted"),
            "prix_min_vinted": data.get("prix_min_vinted"),
            "prix_max_vinted": data.get("prix_max_vinted"),
            "nb_annonces_vinted": data.get("nb_annonces_vinted") or 0,
        },
        "listings": data.get("listings") or [],
    }


# ─── eBay ──────────────────────────────────────────────────────────────────

@app.get("/api/ebay/active", response_model=EbayActiveResponse)
async def ebay_active(q: str = Query(..., min_length=2, description="Mot-clé recherche eBay")):
    """Annonces actives eBay (Browse REST API) + stats prix demandés."""
    keyword = q.strip()
    try:
        listings = await fetch_active_listings(keyword)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("eBay Browse HTTP %s: %s", exc.response.status_code, exc)
        raise HTTPException(502, f"eBay Browse API: {exc.response.status_code}") from exc

    stats = stats_from_active_listings(listings)
    return {
        "keyword": keyword,
        "stats": stats,
        "listings": [
            {
                "titre": l.titre,
                "prix": l.prix,
                "devise": l.devise,
                "url_ebay": l.url_ebay,
                "image_url": l.image_url,
                "item_id": l.item_id,
                "condition": l.condition,
            }
            for l in listings
        ],
    }


@app.get("/api/ebay/sold/{pokedex_id}")
async def ebay_sold(pokedex_id: UUID):
    """Ventes eBay (30 jours) pour une carte Pokédex (cache Supabase 6h)."""
    cached = get_cached_sales(str(pokedex_id))
    if cached:
        return {"cached": True, "sales": cached}

    sb = get_supabase()
    card = (
        sb.table("pokedex")
        .select("id, nom, extension, langue, ebay_keyword, ebay_url")
        .eq("id", str(pokedex_id))
        .single()
        .execute()
    )
    if not card.data:
        raise HTTPException(404, "Carte Pokédex introuvable")
    c = card.data
    result = await sync_pokedex_sales(
        str(pokedex_id),
        c.get("nom") or "",
        c.get("extension") or "",
        langue=c.get("langue") or "FR",
        ebay_keyword=c.get("ebay_keyword"),
        ebay_url=c.get("ebay_url"),
    )
    return {"cached": False, **result}


@app.post("/api/ebay/sync")
async def ebay_sync():
    """Synchronise toutes les cartes du Pokédex avec eBay (30j + MAJ champs)."""
    sb = get_supabase()
    cards = (
        sb.table("pokedex")
        .select("id, nom, extension, langue, ebay_keyword, ebay_url")
        .order("nom")
        .execute()
        .data
        or []
    )
    ok, errors = 0, []
    for c in cards:
        try:
            await sync_pokedex_sales(
                str(c["id"]),
                c.get("nom") or "",
                c.get("extension") or "",
                langue=c.get("langue") or "FR",
                ebay_keyword=c.get("ebay_keyword"),
                ebay_url=c.get("ebay_url"),
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"id": str(c["id"]), "nom": c.get("nom"), "error": str(exc)})
    return {"success": True, "synced": ok, "total": len(cards), "errors": errors}


@app.get("/api/ebay/trending")
async def ebay_trending(categorie: str = Query("all", description="all|pokemon|nba")):
    """
    Top 20 items les plus vendus sur 7 jours.
    Source: snapshots Supabase (table trending_items). Si vide, tente un snapshot rapide.
    """
    sb = get_supabase()
    latest = sb.table("trending_items").select("date_snapshot").order("date_snapshot", desc=True).limit(1).execute().data
    if latest:
        snap = latest[0]["date_snapshot"]
        q = sb.table("trending_items").select("*").eq("date_snapshot", snap)
        if categorie == "pokemon":
            q = q.eq("categorie", "Pokemon Trading Cards")
        elif categorie == "nba":
            q = q.eq("categorie", "NBA Memorabilia")
        rows = q.order("nb_ventes_7j", desc=True).limit(20).execute().data or []
        return {"date_snapshot": snap, "items": rows}

    # fallback: génération rapide depuis eBay (best-effort)
    categories = [("183454", "Pokemon Trading Cards"), ("214", "NBA Memorabilia")]
    items: list[dict] = []
    for cat_id, cat_name in categories:
        if categorie == "pokemon" and cat_id != "183454":
            continue
        if categorie == "nba" and cat_id != "214":
            continue
        try:
            sales = await fetch_sold_items(keywords=None, category_id=cat_id, days=7)
        except Exception as exc:
            logger.warning("Trending eBay %s: %s", cat_id, exc)
            continue
        by_title: dict[str, list[float]] = {}
        url_by_title: dict[str, str] = {}
        for s in sales:
            by_title.setdefault(s.titre, [])
            if s.prix_vente:
                by_title[s.titre].append(float(s.prix_vente))
            if s.url_ebay and s.titre not in url_by_title:
                url_by_title[s.titre] = s.url_ebay
        for title, prices in by_title.items():
            if not prices:
                continue
            avg = round(sum(prices) / len(prices), 2)
            items.append(
                {
                    "titre": title,
                    "categorie": cat_name,
                    "nb_ventes_7j": len(prices),
                    "prix_moyen": avg,
                    "variation_prix_pct": None,
                    "url_ebay": url_by_title.get(title),
                }
            )
    items.sort(key=lambda x: x.get("nb_ventes_7j") or 0, reverse=True)
    return {"date_snapshot": None, "items": items[:20]}


@app.get("/api/historique/{card_id}")
async def historique(card_id: UUID):
    sb = get_supabase()
    rows = (
        sb.table("historique_prix")
        .select("*")
        .eq("pokedex_id", str(card_id))
        .order("date", desc=True)
        .execute()
        .data
        or []
    )
    return rows


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
