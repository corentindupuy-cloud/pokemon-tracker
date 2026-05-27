# Pokémon Tracker (Web)

Application web de gestion achat/revente de cartes Pokémon.

## Stack

- **Backend** : FastAPI
- **BDD** : Supabase (PostgreSQL)
- **Scraping** : Playwright + playwright-stealth
- **Frontend** : HTML / CSS / JS vanilla
- **Déploiement** : Railway (Docker)

## Installation locale

```bash
cd pokemon-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# Renseigner SUPABASE_URL et SUPABASE_KEY
```

### Supabase

1. Créer un projet sur [supabase.com](https://supabase.com)
2. Exécuter `supabase_schema.sql` dans l'éditeur SQL
3. Copier l'URL et la clé **service_role** (ou anon + RLS) dans `.env`

### Lancer l'app

```bash
uvicorn main:app --reload --port 8000
```

Ouvrir http://localhost:8000

## Migration depuis Google Sheets

```bash
# .env : SUPABASE_* + GOOGLE_CREDENTIALS_PATH + SPREADSHEET_ID
python migrate.py
```

## API principale

| Méthode | Route | Description |
|---------|-------|-------------|
| GET | `/` | Interface web |
| GET | `/api/pokedex` | Liste cartes |
| POST | `/api/pokedex` | Ajouter URL (+ scrape auto) |
| POST | `/api/pokedex/{id}/scrape` | Scraper une carte |
| POST | `/api/scrape/all` | Scraper toutes les cartes |
| GET | `/api/stock` | Stock |
| POST | `/api/stock` | Ajouter au stock |
| GET | `/api/radar` | Radar |
| GET | `/api/dashboard` | KPIs |
| GET | `/api/image-proxy?url=` | Proxy images CardMarket |

## Scheduler

Scraping automatique **tous les jours à 8h00** (APScheduler intégré à FastAPI).

## Déploiement Railway

1. Créer un projet Railway lié au repo
2. Le `Dockerfile` installe Playwright + Chromium
3. Variables d'environnement :
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
   - `PORT` (optionnel, défaut 8000)

## Structure

```
pokemon-tracker/
├── main.py
├── scraper.py
├── database.py
├── models.py
├── services.py
├── migrate.py
├── supabase_schema.sql
├── static/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── Dockerfile
├── railway.toml
└── requirements.txt
```
