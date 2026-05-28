"""
Scraping CardMarket via Playwright + playwright-stealth.
Extrait depuis la logique de pokemon-cotes/update_cotes.py.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse

import httpx
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)
log = logger


def _diag(msg: str, *args: object) -> None:
    text = msg % args if args else msg
    log.info(text)
    print(text, flush=True)

BASE_URL = "https://www.cardmarket.com"
POKEMON_SEARCH_URL = f"{BASE_URL}/fr/Pokemon/Products/Search"
PAGE_TIMEOUT_MS = 45_000
DEFAULT_DELAY = 2.5
EBAY_FINDING_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
EBAY_CATEGORY_TCG = "183454"
EBAY_GLOBAL_ID = "EBAY-FR"
EBAY_SOLD_DAYS = 30
EBAY_AVG_SAMPLE = 10
USD_TO_EUR = float(os.getenv("USD_TO_EUR", "0.92"))

CONDITION_MAP = {
    "mint": 1, "mt": 1, "neuf": 1,
    "near mint": 2, "nm": 2, "presque neuf": 2,
    "excellent": 3, "ex": 3,
    "good": 4, "gd": 4, "bon": 4,
    "light played": 5, "lp": 5,
    "played": 6, "pl": 6,
    "poor": 7, "po": 7,
}

EXTRACT_PRICES_JS = """
() => {
  const priceKeys = ['prix tendance', 'trend price', 'prix moyen', 'average', 'avg. sale'];
  const trendKeys = ['7 jour', '7 day', '7j', '7d', '7 days'];
  let priceText = null;
  let trend7d = null;
  document.querySelectorAll('dl').forEach(dl => {
    const dts = [...dl.querySelectorAll('dt')];
    const dds = [...dl.querySelectorAll('dd')];
    dts.forEach((dt, i) => {
      const label = (dt.innerText || '').toLowerCase().trim();
      const value = (dds[i]?.innerText || '').trim();
      if (!value) return;
      if (priceKeys.some(k => label.includes(k)) && !priceText) priceText = value;
      if (trendKeys.some(k => label.includes(k)) && !trend7d) trend7d = value;
    });
  });
  if (!priceText) {
    for (const sel of ['.col-price', '.price-container', '[class*="trend"]']) {
      const el = document.querySelector(sel);
      if (el && el.innerText && /\\d/.test(el.innerText)) {
        priceText = el.innerText.trim();
        break;
      }
    }
  }
  return { priceText, trend7d };
}
"""

EXTRACT_META_JS = """
() => {
  const BAD = /logo|banner|layout|icon/i;
  const CARD_PATH = /\\/img\\/(cards|Magic|Pokemon)\\//i;
  const PRODUCT_CDN = /product-images/i;

  const isRejected = (src) => !src || BAD.test(src);
  const isCardImgPath = (src) => CARD_PATH.test(src);
  const isProductCdn = (src) => PRODUCT_CDN.test(src);
  const resolveSrc = (src) => {
    if (!src) return null;
    try {
      return new URL(src, document.baseURI).href;
    } catch {
      return src;
    }
  };
  const imgSrc = (img) =>
    resolveSrc(img.src || img.getAttribute('data-src') || img.getAttribute('data-original'));

  const pickFromImgs = (imgs) => {
    for (const img of imgs) {
      const src = imgSrc(img);
      if (src && !isRejected(src) && isCardImgPath(src)) return src;
    }
    for (const img of imgs) {
      const src = imgSrc(img);
      if (src && !isRejected(src) && isProductCdn(src)) return src;
    }
    return null;
  };

  const ogImg = document.querySelector('meta[property="og:image"]');
  const ogTitle = document.querySelector('meta[property="og:title"]');
  const h1 = document.querySelector('h1');
  const breadcrumb = document.querySelector('.breadcrumb, nav[aria-label="breadcrumb"]');
  let imageUrl = null;

  if (ogImg && ogImg.content) {
    const og = resolveSrc(ogImg.content.trim());
    if (!isRejected(og)) imageUrl = og;
  }

  if (!imageUrl) {
    const hero = document.querySelector(
      '.product-image img, #product-image img, [itemprop="image"], .gallery__image img, .slide img'
    );
    if (hero) imageUrl = pickFromImgs([hero]);
  }

  if (!imageUrl) {
    const slug = (location.pathname.split('/').pop() || '').split('?')[0];
    if (slug) {
      const rowLink = document.querySelector(`a[href*="${slug}"]`);
      if (rowLink) {
        const row = rowLink.closest('tr, .row, .article-row, .product-row, [data-product-id], li, div');
        if (row) imageUrl = pickFromImgs([...row.querySelectorAll('img')]);
      }
    }
  }

  if (!imageUrl) {
    imageUrl = pickFromImgs([...document.querySelectorAll('img')]);
  }

  let title = (ogTitle && ogTitle.content) || (h1 && h1.innerText.trim()) || '';
  let expansion = '';
  if (breadcrumb) {
    const parts = [...breadcrumb.querySelectorAll('a, span')].map(e => e.innerText.trim()).filter(Boolean);
    if (parts.length >= 2) expansion = parts[parts.length - 2];
  }
  const sub = document.querySelector('.product-info h2, .text-muted, .expansion-name');
  if (sub && !expansion) expansion = sub.innerText.trim();
  return { title, expansion, imageUrl };
}
"""


_BAD_IMAGE_RE = re.compile(r"logo|banner|layout|icon", re.I)
_CARD_IMAGE_PATH_RE = re.compile(r"/img/(?:cards|Magic|Pokemon)/", re.I)


def is_rejected_image_url(url: str) -> bool:
    return bool(_BAD_IMAGE_RE.search(url))


def normalize_image_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("/"):
        u = BASE_URL + u
    if is_rejected_image_url(u):
        return None
    if _CARD_IMAGE_PATH_RE.search(u):
        return u
    if "product-images" in u:
        return u
    return None


@dataclass
class ScrapeData:
    nom: str
    extension: str
    prix_actuel: Optional[float]
    tendance_7j: Optional[float]
    image_url: Optional[str]
    url_cardmarket: str
    error: Optional[str] = None


@dataclass
class EbaySoldData:
    prix_moyen_ebay: Optional[float] = None
    prix_min_ebay: Optional[float] = None
    prix_max_ebay: Optional[float] = None
    nb_ventes_ebay: int = 0
    date_maj_ebay: Optional[datetime] = None
    error: Optional[str] = None


def parse_euro_price(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = text.replace("\xa0", " ").replace("€", "").replace("EUR", "").strip()
    match = re.search(r"[\d\s.,]+", cleaned)
    if not match:
        return None
    num = match.group().replace(" ", "").replace(",", ".")
    if num.count(".") > 1:
        parts = num.split(".")
        num = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(num)
    except ValueError:
        return None


def parse_trend_float(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"[-+]?\d+[.,]?\d*", text.replace(",", "."))
    return float(m.group().replace(",", ".")) if m else None


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def condition_to_min(condition: str) -> int:
    return CONDITION_MAP.get(normalize(condition), 2)


def build_product_url(base_url: str, condition: str = "Near Mint") -> str:
    parsed = urlparse(base_url.strip())
    if "cardmarket.com" not in parsed.netloc:
        raise ValueError("URL CardMarket invalide")
    path = parsed.path.rstrip("/")
    query = parse_qs(parsed.query)
    query["language"] = ["7"]
    query["minCondition"] = [str(condition_to_min(condition))]
    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunparse((parsed.scheme, parsed.netloc, path, "", new_query, ""))


def build_ebay_keywords(nom: str, extension: str = "") -> str:
    parts = [nom.strip(), extension.strip(), "Pokemon"]
    return " ".join(p for p in parts if p)


def _ebay_first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _ebay_price_eur(price_node: Any) -> Optional[float]:
    node = _ebay_first(price_node)
    if not isinstance(node, dict):
        return None
    raw = node.get("__value__") or node.get("value")
    if raw is None:
        return None
    try:
        amount = float(str(raw).replace(",", "."))
    except ValueError:
        return None
    currency = (node.get("@currencyId") or node.get("currencyId") or "EUR").upper()
    if currency == "EUR":
        return amount
    if currency == "USD":
        return round(amount * USD_TO_EUR, 2)
    return amount


def _parse_ebay_items(payload: dict[str, Any]) -> tuple[list[float], int]:
    root = _ebay_first(payload.get("findCompletedItemsResponse"))
    if not isinstance(root, dict):
        return [], 0
    ack = str(_ebay_first(root.get("ack")) or "").lower()
    if ack and ack not in ("success", "warning"):
        return [], 0
    search = _ebay_first(root.get("searchResult"))
    if not isinstance(search, dict):
        return [], 0
    try:
        total = int(search.get("@count") or 0)
    except (TypeError, ValueError):
        total = 0
    items = search.get("item") or []
    if isinstance(items, dict):
        items = [items]
    prices: list[float] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        status = _ebay_first(item.get("sellingStatus"))
        if not isinstance(status, dict):
            continue
        price = _ebay_price_eur(status.get("currentPrice"))
        if price is not None and price > 0:
            prices.append(price)
    return prices, total or len(prices)


def _scrape_ebay_sold_sync(nom: str, extension: str = "") -> EbaySoldData:
    now = datetime.now(timezone.utc)
    api_key = os.getenv("EBAY_APP_ID", "").strip() or os.getenv("EBAY_API_KEY", "").strip()
    keywords = build_ebay_keywords(nom, extension)
    if not api_key:
        return EbaySoldData(error="EBAY_APP_ID manquant", date_maj_ebay=now)
    if not keywords.replace("Pokemon", "").strip():
        return EbaySoldData(error="Nom carte requis pour eBay", date_maj_ebay=now)

    end_from = (now - timedelta(days=EBAY_SOLD_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": api_key,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "keywords": keywords,
        "categoryId": EBAY_CATEGORY_TCG,
        "GLOBAL-ID": EBAY_GLOBAL_ID,
        "sortOrder": "EndTimeSoonest",
        "paginationInput.entriesPerPage": "100",
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "itemFilter(1).name": "EndTimeFrom",
        "itemFilter(1).value": end_from,
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(EBAY_FINDING_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        logger.warning("eBay Finding API: %s", exc)
        return EbaySoldData(error=str(exc), date_maj_ebay=now)

    prices, total = _parse_ebay_items(payload)
    if not prices:
        return EbaySoldData(nb_ventes_ebay=0, date_maj_ebay=now)

    sample = prices[:EBAY_AVG_SAMPLE]
    return EbaySoldData(
        prix_moyen_ebay=round(sum(sample) / len(sample), 2),
        prix_min_ebay=round(min(sample), 2),
        prix_max_ebay=round(max(sample), 2),
        nb_ventes_ebay=total,
        date_maj_ebay=now,
    )


async def scrape_ebay_sold(nom: str, extension: str = "") -> EbaySoldData:
    """Prix des ventes eBay terminées (30 derniers jours) via Finding API."""
    return await asyncio.to_thread(_scrape_ebay_sold_sync, nom, extension)


async def is_cloudflare_page(page: Page) -> bool:
    try:
        title = (await page.title()).lower()
        content = await page.content()
    except Exception:
        return False
    markers = ("just a moment", "cf_chl", "challenge-platform", "enable javascript")
    combined = (title + content).lower()
    return any(m in combined for m in markers)


async def human_delay(min_s: float = 0.8, max_s: float = 2.5) -> None:
    await asyncio.sleep(random.uniform(min_s, max_s))


async def simulate_human(page: Page) -> None:
    """Scroll et mouvements souris pour imiter un utilisateur (identique à update_cotes.py)."""
    await human_delay(0.4, 1.2)
    scroll = random.randint(150, 700)
    await page.evaluate(f"window.scrollBy({{ top: {scroll}, behavior: 'smooth' }})")
    await human_delay(0.3, 0.9)
    try:
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        w, h = viewport["width"], viewport["height"]
        await page.mouse.move(
            random.randint(80, max(100, w - 80)),
            random.randint(80, max(100, h - 80)),
            steps=random.randint(8, 20),
        )
    except Exception:
        pass
    await human_delay(0.2, 0.7)


# User-Agent identique à update_cotes.py
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]


class CardMarketScraper:
    """Navigateur Chromium headless avec playwright-stealth (API async)."""

    def __init__(self, delay: float = DEFAULT_DELAY) -> None:
        self.delay = delay
        self._playwright = None
        self._browser = None
        self._context = None
        self._page: Optional[Page] = None
        self._stealth = Stealth(navigator_languages_override=("fr-FR", "fr"))
        self._cm = None

    async def __aenter__(self) -> CardMarketScraper:
        _diag("Playwright: demarrage (stealth + async_playwright)")
        self._cm = self._stealth.use_async(async_playwright())
        self._playwright = await self._cm.__aenter__()
        _diag("Playwright launch() appele")
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=BROWSER_ARGS,
        )
        _diag("Playwright: browser lance, creation contexte")
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="fr-FR",
            timezone_id="Europe/Paris",
            user_agent=USER_AGENT,
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(PAGE_TIMEOUT_MS)
        _diag("Playwright: page prete timeout=%sms", PAGE_TIMEOUT_MS)
        return self

    async def __aexit__(self, *args: object) -> None:
        _diag("Playwright: fermeture navigateur")
        if self._browser:
            await self._browser.close()
        if self._cm is not None:
            await self._cm.__aexit__(*args)

    async def _throttle(self) -> None:
        await human_delay(self.delay * 0.8, self.delay * 1.4)

    async def _goto(self, url: str) -> None:
        assert self._page is not None
        await self._throttle()
        logger.debug("Navigation → %s", url)
        await self._page.goto(url, wait_until="domcontentloaded")
        await simulate_human(self._page)
        if await is_cloudflare_page(self._page):
            await human_delay(2.0, 4.0)
            await simulate_human(self._page)
            if await is_cloudflare_page(self._page):
                raise RuntimeError("Page Cloudflare non contournée")

    async def scrape_url(self, url: str, etat: str = "Near Mint") -> ScrapeData:
        _diag("scrape_url: %s etat=%s", url, etat)
        try:
            full_url = build_product_url(url, etat)
            _diag("URL produit: %s", full_url)
            await self._goto(full_url)
            assert self._page
            prices = await self._page.evaluate(EXTRACT_PRICES_JS)
            meta = await self._page.evaluate(EXTRACT_META_JS)
            prix = parse_euro_price(prices.get("priceText") or "")
            trend = parse_trend_float(prices.get("trend7d"))
            nom = (meta.get("title") or "").strip() or "Carte inconnue"
            extension = (meta.get("extension") or "").strip()
            image = normalize_image_url(meta.get("imageUrl"))
            clean_url = url.split("?")[0]
            if not prix:
                return ScrapeData(
                    nom=nom,
                    extension=extension,
                    prix_actuel=None,
                    tendance_7j=trend,
                    image_url=image,
                    url_cardmarket=clean_url,
                    error="Prix non trouvé",
                )
            return ScrapeData(
                nom=nom,
                extension=extension,
                prix_actuel=prix,
                tendance_7j=trend,
                image_url=image,
                url_cardmarket=clean_url,
            )
        except PlaywrightTimeout:
            return ScrapeData(
                nom="",
                extension="",
                prix_actuel=None,
                tendance_7j=None,
                image_url=None,
                url_cardmarket=url,
                error="Timeout",
            )
        except RuntimeError as exc:
            return ScrapeData(
                nom="",
                extension="",
                prix_actuel=None,
                tendance_7j=None,
                image_url=None,
                url_cardmarket=url,
                error=str(exc),
            )
        except Exception as exc:
            return ScrapeData(
                nom="",
                extension="",
                prix_actuel=None,
                tendance_7j=None,
                image_url=None,
                url_cardmarket=url,
                error=str(exc),
            )


async def scrape_cardmarket_url(
    url: str, etat: str = "Near Mint", delay: float = DEFAULT_DELAY
) -> ScrapeData:
    """Scrape CardMarket (Playwright async, compatible FastAPI)."""
    _diag("CardMarketScraper: ouverture navigateur url=%s etat=%s", url, etat)
    async with CardMarketScraper(delay=delay) as scraper:
        result = await scraper.scrape_url(url, etat)
        _diag(
            "CardMarketScraper termine: prix=%s nom=%r error=%r",
            result.prix_actuel,
            result.nom,
            result.error,
        )
        return result


async def scrape_multiple(urls: list[tuple[str, str]]) -> list[ScrapeData]:
    """Scrape séquentiel (un navigateur, API async)."""
    results: list[ScrapeData] = []
    async with CardMarketScraper() as scraper:
        for url, etat in urls:
            results.append(await scraper.scrape_url(url, etat))
    return results
