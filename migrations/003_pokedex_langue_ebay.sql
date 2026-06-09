-- Langue + recherche eBay personnalisée par carte
alter table pokedex add column if not exists langue text default 'FR';
alter table pokedex add column if not exists ebay_keyword text;
alter table pokedex add column if not exists ebay_url text;
