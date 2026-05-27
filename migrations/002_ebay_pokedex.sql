-- Colonnes eBay (ventes terminées 30j) sur pokedex
alter table pokedex add column if not exists prix_moyen_ebay float;
alter table pokedex add column if not exists prix_min_ebay float;
alter table pokedex add column if not exists prix_max_ebay float;
alter table pokedex add column if not exists nb_ventes_ebay int default 0;
alter table pokedex add column if not exists date_maj_ebay timestamptz;
