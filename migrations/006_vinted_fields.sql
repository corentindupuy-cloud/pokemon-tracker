-- Vinted + eBay actif + prix de référence médiane
alter table pokedex add column if not exists prix_moyen_vinted float;
alter table pokedex add column if not exists prix_min_vinted float;
alter table pokedex add column if not exists prix_max_vinted float;
alter table pokedex add column if not exists nb_annonces_vinted int default 0;
alter table pokedex add column if not exists date_maj_vinted timestamptz;

alter table pokedex add column if not exists prix_actif_ebay float;
alter table pokedex add column if not exists nb_annonces_ebay_actif int default 0;
alter table pokedex add column if not exists date_maj_ebay_actif timestamptz;

alter table pokedex add column if not exists prix_reference_mediane float;

-- Historique multi-sources
alter table historique_prix add column if not exists prix_ebay_actif float;
alter table historique_prix add column if not exists prix_vinted float;
alter table historique_prix add column if not exists prix_mediane float;
