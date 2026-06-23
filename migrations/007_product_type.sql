-- Type produit + identifiants précis
alter table pokedex add column if not exists type_produit text default 'single';
alter table pokedex add column if not exists numero_carte text;
alter table pokedex add column if not exists code_set text;
alter table pokedex add column if not exists nom_en text;
