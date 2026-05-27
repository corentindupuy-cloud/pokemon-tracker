-- eBay: ventes terminées + snapshots de tendances
-- À exécuter dans Supabase (SQL editor)

create extension if not exists "uuid-ossp";

create table if not exists ebay_sales (
  id uuid primary key default gen_random_uuid(),
  pokedex_id uuid references pokedex(id) on delete set null,
  titre text not null,
  prix_vente float,
  date_vente timestamptz,
  categorie text,
  url_ebay text,
  nb_ventes_7j int,
  prix_moyen_7j float,
  prix_min_7j float,
  prix_max_7j float,
  created_at timestamptz default now()
);

create index if not exists idx_ebay_sales_pokedex_id on ebay_sales(pokedex_id);
create index if not exists idx_ebay_sales_date_vente on ebay_sales(date_vente desc);

create table if not exists trending_items (
  id uuid primary key default gen_random_uuid(),
  titre text not null,
  categorie text,
  nb_ventes_7j int,
  prix_moyen float,
  variation_prix_pct float,
  url_ebay text,
  date_snapshot date not null,
  created_at timestamptz default now()
);

create index if not exists idx_trending_snapshot on trending_items(date_snapshot desc);
create index if not exists idx_trending_categorie on trending_items(categorie);

