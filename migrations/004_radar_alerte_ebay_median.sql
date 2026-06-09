-- Alerte radar + stats eBay enrichies
alter table radar add column if not exists alerte_active boolean default false;
alter table radar add column if not exists marge_minimum float;

alter table pokedex add column if not exists prix_median_ebay float;
alter table pokedex add column if not exists date_vente_plus_recente timestamptz;
