-- À exécuter dans Supabase si la table radar existe déjà
alter table radar add column if not exists priorite int check (priorite >= 1 and priorite <= 5);
