-- Schéma Supabase pour Pokémon Tracker
-- Exécuter dans l'éditeur SQL Supabase

create extension if not exists "uuid-ossp";

create table if not exists pokedex (
    id uuid primary key default gen_random_uuid(),
    nom text not null,
    extension text default '',
    etat text default 'Near Mint',
    url_cardmarket text unique not null,
    image_url text,
    prix_actuel float,
    tendance_7j float,
    prix_moyen_ebay float,
    prix_min_ebay float,
    prix_max_ebay float,
    nb_ventes_ebay int default 0,
    date_maj_ebay timestamptz,
    langue text default 'FR',
    ebay_keyword text,
    ebay_url text,
    prix_median_ebay float,
    date_vente_plus_recente timestamptz,
    derniere_maj timestamptz,
    created_at timestamptz default now()
);

create table if not exists stock (
    id uuid primary key default gen_random_uuid(),
    pokedex_id uuid references pokedex(id) on delete set null,
    ref text,
    prix_achat float,
    date_achat date,
    statut text default 'En stock',
    source text,
    quantite int default 1,
    notes text,
    created_at timestamptz default now()
);

create table if not exists ventes (
    id uuid primary key default gen_random_uuid(),
    stock_id uuid references stock(id) on delete cascade,
    plateforme text,
    prix_vente float,
    date_vente date,
    frais_plateforme float default 0,
    notes text,
    created_at timestamptz default now()
);

create table if not exists radar (
    id uuid primary key default gen_random_uuid(),
    pokedex_id uuid references pokedex(id) on delete cascade,
    prix_cible float,
    priorite int check (priorite >= 1 and priorite <= 5),
    source_potentielle text,
    marge_minimum float,
    alerte_active boolean default false,
    urgence text,
    statut text default 'Actif',
    notes text,
    created_at timestamptz default now()
);

-- Si la table radar existe déjà :
-- alter table radar add column if not exists priorite int check (priorite >= 1 and priorite <= 5);

create table if not exists historique_prix (
    id uuid primary key default gen_random_uuid(),
    pokedex_id uuid references pokedex(id) on delete cascade,
    prix float not null,
    tendance_7j float,
    variation_j1_eur float,
    variation_j1_pct float,
    variation_j0_eur float,
    variation_j0_pct float,
    date date default current_date,
    created_at timestamptz default now()
);

create index if not exists idx_stock_pokedex on stock(pokedex_id);
create index if not exists idx_stock_statut on stock(statut);
create index if not exists idx_radar_pokedex on radar(pokedex_id);
create index if not exists idx_historique_pokedex on historique_prix(pokedex_id);
create index if not exists idx_historique_date on historique_prix(date);

-- eBay
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

alter table pokedex enable row level security;
alter table stock enable row level security;
alter table ventes enable row level security;
alter table radar enable row level security;
alter table historique_prix enable row level security;
alter table ebay_sales enable row level security;
alter table trending_items enable row level security;

-- Politiques permissives (service role) — à affiner en production
create policy "allow_all_pokedex" on pokedex for all using (true) with check (true);
create policy "allow_all_stock" on stock for all using (true) with check (true);
create policy "allow_all_ventes" on ventes for all using (true) with check (true);
create policy "allow_all_radar" on radar for all using (true) with check (true);
create policy "allow_all_historique" on historique_prix for all using (true) with check (true);
create policy "allow_all_ebay_sales" on ebay_sales for all using (true) with check (true);
create policy "allow_all_trending_items" on trending_items for all using (true) with check (true);
