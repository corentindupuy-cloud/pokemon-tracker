from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class PokedexBase(BaseModel):
    nom: str = ""
    extension: str = ""
    etat: str = "Near Mint"
    url_cardmarket: str
    image_url: Optional[str] = None
    prix_actuel: Optional[float] = None
    tendance_7j: Optional[float] = None


LANGUES_POKEDEX = frozenset({"FR", "EN", "JP", "IT", "DE", "ES"})


class PokedexCreate(BaseModel):
    url_cardmarket: str
    langue: str = "FR"
    ebay_keyword: Optional[str] = None
    ebay_url: Optional[str] = None


class SearchResultOut(BaseModel):
    nom: str
    extension: str = ""
    image_url: Optional[str] = None
    prix_actuel: Optional[float] = None
    url_cardmarket: str


class PokedexOut(PokedexBase):
    id: UUID
    prix_moyen_ebay: Optional[float] = None
    prix_median_ebay: Optional[float] = None
    prix_min_ebay: Optional[float] = None
    prix_max_ebay: Optional[float] = None
    nb_ventes_ebay: Optional[int] = None
    date_maj_ebay: Optional[datetime] = None
    prix_actif_ebay: Optional[float] = None
    nb_annonces_ebay_actif: Optional[int] = None
    date_maj_ebay_actif: Optional[datetime] = None
    prix_moyen_vinted: Optional[float] = None
    prix_min_vinted: Optional[float] = None
    prix_max_vinted: Optional[float] = None
    nb_annonces_vinted: Optional[int] = None
    date_maj_vinted: Optional[datetime] = None
    prix_reference_mediane: Optional[float] = None
    date_vente_plus_recente: Optional[datetime] = None
    langue: str = "FR"
    ebay_keyword: Optional[str] = None
    ebay_url: Optional[str] = None
    derniere_maj: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class StockCreate(BaseModel):
    pokedex_id: UUID
    ref: Optional[str] = None
    prix_achat: float
    date_achat: Optional[date] = None
    statut: str = "En stock"
    source: Optional[str] = None
    quantite: int = Field(1, ge=1)
    notes: Optional[str] = None


class StockUpdate(BaseModel):
    statut: Optional[str] = None
    prix_achat: Optional[float] = None
    quantite: Optional[int] = Field(None, ge=1)
    notes: Optional[str] = None


class StockOut(BaseModel):
    id: UUID
    pokedex_id: Optional[UUID] = None
    ref: Optional[str] = None
    prix_achat: Optional[float] = None
    date_achat: Optional[date] = None
    statut: str = "En stock"
    source: Optional[str] = None
    quantite: int = Field(1, ge=1)
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    # jointures
    nom: Optional[str] = None
    extension: Optional[str] = None
    image_url: Optional[str] = None
    prix_actuel: Optional[float] = None
    prix_reference_mediane: Optional[float] = None
    prix_actif_ebay: Optional[float] = None
    prix_moyen_vinted: Optional[float] = None
    marge_latente: Optional[float] = None


class RadarCreate(BaseModel):
    pokedex_id: UUID
    prix_cible: float
    priorite: Optional[int] = Field(None, ge=1, le=5, description="1 = priorité max")
    source_potentielle: Optional[str] = None
    marge_minimum: Optional[float] = None
    statut: str = "Actif"
    notes: Optional[str] = None
    alerte_active: bool = False


class RadarUpdate(BaseModel):
    priorite: Optional[int] = Field(None, ge=1, le=5)
    prix_cible: Optional[float] = None
    statut: Optional[str] = None
    notes: Optional[str] = None
    marge_minimum: Optional[float] = None
    alerte_active: Optional[bool] = None


class RadarOut(BaseModel):
    id: UUID
    pokedex_id: UUID
    prix_cible: float
    priorite: Optional[int] = None
    source_potentielle: Optional[str] = None
    marge_minimum: Optional[float] = None
    urgence: Optional[str] = None
    statut: str = "Actif"
    notes: Optional[str] = None
    alerte_active: bool = False
    created_at: Optional[datetime] = None
    nom: Optional[str] = None
    extension: Optional[str] = None
    image_url: Optional[str] = None
    prix_actuel: Optional[float] = None
    prix_reference_mediane: Optional[float] = None
    prix_actif_ebay: Optional[float] = None
    prix_moyen_vinted: Optional[float] = None


class VenteCreate(BaseModel):
    stock_id: UUID
    prix_vente: float
    date_vente: Optional[date] = None
    frais_plateforme: float = 0
    plateforme: Optional[str] = None
    notes: Optional[str] = None


class VenteOut(BaseModel):
    id: UUID
    stock_id: UUID
    plateforme: Optional[str] = None
    prix_vente: Optional[float] = None
    date_vente: Optional[date] = None
    frais_plateforme: float = 0
    notes: Optional[str] = None
    nom: Optional[str] = None
    ref: Optional[str] = None
    prix_achat: Optional[float] = None
    benefice: Optional[float] = None


class HistoriqueOut(BaseModel):
    id: UUID
    pokedex_id: UUID
    prix: float
    tendance_7j: Optional[float] = None
    variation_j1_eur: Optional[float] = None
    variation_j1_pct: Optional[float] = None
    variation_j0_eur: Optional[float] = None
    variation_j0_pct: Optional[float] = None
    date: date


class DashboardCharts(BaseModel):
    labels: list[str]
    valeur_stock: list[float]
    valeur_stock_cm: list[float] = []
    valeur_stock_ebay: list[float] = []
    valeur_stock_mediane: list[float] = []
    chiffre_affaires: list[float]


class DashboardKPIs(BaseModel):
    capital_investi: float = 0
    valeur_stock: float = 0
    valeur_stock_cm: float = 0
    valeur_stock_ebay: float = 0
    valeur_stock_vinted: float = 0
    marge_latente_totale: float = 0
    ca_total: float = 0
    benefice_net: float = 0
    marge_moyenne_pct: float = 0
    nb_cartes_pokedex: int = 0
    nb_en_stock: int = 0
    nb_radar: int = 0
    nb_vendus: int = 0


class OpportunityItem(BaseModel):
    type: str
    nom: str
    extension: Optional[str] = None
    score: float = 0
    detail: str = ""
    pokedex_id: Optional[str] = None


class DashboardFullOut(DashboardKPIs):
    opportunities: list[OpportunityItem] = []
    top_marges: list[dict] = []


class ScrapeResultOut(BaseModel):
    success: bool
    pokedex_id: Optional[UUID] = None
    nom: Optional[str] = None
    prix_actuel: Optional[float] = None
    prix_moyen_ebay: Optional[float] = None
    image_url: Optional[str] = None
    error: Optional[str] = None


class EbayActiveListingOut(BaseModel):
    titre: str
    prix: Optional[float] = None
    devise: str = "EUR"
    url_ebay: Optional[str] = None
    image_url: Optional[str] = None
    item_id: Optional[str] = None
    condition: Optional[str] = None


class EbayActiveStatsOut(BaseModel):
    prix_moyen: Optional[float] = None
    prix_min: Optional[float] = None
    prix_max: Optional[float] = None
    nb_annonces: int = 0
    source: str = "browse_active"


class EbayActiveResponse(BaseModel):
    keyword: str
    stats: EbayActiveStatsOut
    listings: list[EbayActiveListingOut]


class VintedListingOut(BaseModel):
    titre: str
    prix: Optional[float] = None
    url: Optional[str] = None


class VintedStatsOut(BaseModel):
    prix_moyen_vinted: Optional[float] = None
    prix_min_vinted: Optional[float] = None
    prix_max_vinted: Optional[float] = None
    nb_annonces_vinted: int = 0


class VintedActiveResponse(BaseModel):
    keyword: str
    langue: str = "FR"
    stats: VintedStatsOut
    listings: list[VintedListingOut]


class MarketSyncResult(BaseModel):
    success: bool
    synced: int = 0
    total: int = 0
    errors: list[dict] = []
