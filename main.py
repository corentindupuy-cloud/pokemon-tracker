"""
Pokémon Tracker — API FastAPI + interface web.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Optional
from uuid import UUID

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from database import get_supabase
from models import (
    DashboardKPIs,
    PokedexCreate,
    PokedexOut,
    DashboardCharts,
    RadarCreate,
    RadarOut,
    RadarUpdate,
    ScrapeResultOut,
    StockCreate,
    StockOut,
    StockUpdate,
    VenteCreate,
    VenteOut,
)
from services import (
    enrich_stock_row,
    get_dashboard_charts,
    get_dashboard_kpis,
    propagate_radar_urgency,
    scrape_all_cards,
    scrape_and_update_pokedex,
)
from scraper import scrape_cardmarket_url
from ebay import fetch_sold_items, get_cached_sales, sync_pokedex_sales

load_dotenv()


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
    for name in ("services", "scraper", "ebay", "uvicorn", "uvicorn.error", "uvicorn.access"):
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
        cards = sb.table("pokedex").select("id, nom, extension").order("nom").execute().data or []
        ok, err = 0, 0
        for c in cards:
            try:
                await sync_pokedex_sales(str(c["id"]), c.get("nom") or "", c.get("extension") or "")
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(scheduled_scrape_job, "cron", hour=8, minute=0, id="daily_scrape")
    scheduler.add_job(scheduled_ebay_sync_job, "cron", hour=9, minute=0, id="daily_ebay_sync")
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

    ins = sb.table("pokedex").insert(
        {"url_cardmarket": url.split("?")[0], "nom": "Chargement…", "etat": "Near Mint"}
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


# ─── Stock ─────────────────────────────────────────────────────────────────

@app.get("/api/stock")
async def list_stock(statut: Optional[str] = None):
    sb = get_supabase()
    q = sb.table("stock").select("*, pokedex(nom, extension, image_url, prix_actuel)")
    if statut:
        q = q.eq("statut", statut)
    rows = q.order("created_at", desc=True).execute().data or []
    out = []
    for r in rows:
        p = r.pop("pokedex", None) or {}
        row = {**r, "nom": p.get("nom"), "extension": p.get("extension"),
               "image_url": p.get("image_url"), "prix_actuel": p.get("prix_actuel")}
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
        .select("*, pokedex(nom, extension, image_url, prix_actuel)")
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    rows.sort(key=lambda r: (r.get("priorite") is None, -(r.get("priorite") or 0)))
    out = []
    for r in rows:
        p = r.pop("pokedex", None) or {}
        out.append({**r, "nom": p.get("nom"), "extension": p.get("extension"),
                    "image_url": p.get("image_url"), "prix_actuel": p.get("prix_actuel")})
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
    p = sb.table("pokedex").select("prix_actuel").eq("id", pid).execute()
    from services import compute_urgence

    prix_actuel = p.data[0].get("prix_actuel") if p.data else None
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


@app.get("/api/dashboard", response_model=DashboardKPIs)
async def dashboard():
    return get_dashboard_kpis()


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
            "chiffre_affaires": [0],
        }


# ─── eBay ──────────────────────────────────────────────────────────────────

@app.get("/api/ebay/sold/{pokedex_id}")
async def ebay_sold(pokedex_id: UUID):
    """Ventes eBay (30 jours) pour une carte Pokédex (cache Supabase 6h)."""
    cached = get_cached_sales(str(pokedex_id))
    if cached:
        return {"cached": True, "sales": cached}

    sb = get_supabase()
    card = sb.table("pokedex").select("id, nom, extension").eq("id", str(pokedex_id)).single().execute()
    if not card.data:
        raise HTTPException(404, "Carte Pokédex introuvable")
    result = await sync_pokedex_sales(
        str(pokedex_id), card.data.get("nom") or "", card.data.get("extension") or ""
    )
    return {"cached": False, **result}


@app.post("/api/ebay/sync")
async def ebay_sync():
    """Synchronise toutes les cartes du Pokédex avec eBay (30j + MAJ champs)."""
    sb = get_supabase()
    cards = sb.table("pokedex").select("id, nom, extension").order("nom").execute().data or []
    ok, errors = 0, []
    for c in cards:
        try:
            await sync_pokedex_sales(str(c["id"]), c.get("nom") or "", c.get("extension") or "")
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
