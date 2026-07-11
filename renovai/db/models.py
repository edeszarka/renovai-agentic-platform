import uuid
from datetime import date, datetime
from typing import List, Optional
from uuid import uuid4
from sqlalchemy import ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Quote(Base):
    __tablename__ = "quotes"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    file_name: Mapped[str] = mapped_column(unique=True)
    address_raw: Mapped[Optional[str]]
    district: Mapped[Optional[int]]
    quote_date: Mapped[Optional[date]]
    total_labor_huf: Mapped[int]
    total_material_huf: Mapped[int]
    grand_total_huf: Mapped[int]
    grand_total_adjusted_huf: Mapped[Optional[int]]
    inflation_target_date: Mapped[Optional[date]]
    timeline_weeks_min: Mapped[Optional[int]]
    timeline_weeks_max: Mapped[Optional[int]]
    payment_schedule: Mapped[Optional[str]]
    has_slag_complication: Mapped[bool] = mapped_column(default=False)
    area_sqm: Mapped[Optional[float]]
    ceiling_height: Mapped[Optional[float]]
    elevator_type: Mapped[Optional[str]]
    has_gas_heating: Mapped[Optional[bool]]
    floor_number: Mapped[Optional[int]]
    num_line_items: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    line_items: Mapped[List["LineItemORM"]] = relationship(back_populates="quote", cascade="all, delete-orphan")
    not_included: Mapped[List["NotIncluded"]] = relationship(back_populates="quote", cascade="all, delete-orphan")
    buyer_purchases: Mapped[List["BuyerPurchase"]] = relationship(back_populates="quote", cascade="all, delete-orphan")
    general_notes: Mapped[List["GeneralNote"]] = relationship(back_populates="quote", cascade="all, delete-orphan")
    vectors: Mapped[List["QuoteVector"]] = relationship(back_populates="quote", cascade="all, delete-orphan")

class LineItemORM(Base):
    __tablename__ = "line_items"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id"))
    name_hu: Mapped[str]
    category_key: Mapped[Optional[str]] = mapped_column(ForeignKey("work_categories.key"))
    labor_cost_huf: Mapped[Optional[int]]
    material_cost_huf: Mapped[Optional[int]]
    total_cost_huf: Mapped[Optional[int]]
    notes_hu: Mapped[Optional[str]]
    section: Mapped[str]
    version: Mapped[int]
    is_included: Mapped[bool] = mapped_column(default=True)
    
    quote: Mapped["Quote"] = relationship(back_populates="line_items")
    category: Mapped[Optional["WorkCategory"]] = relationship()

class WorkCategory(Base):
    __tablename__ = "work_categories"
    
    key: Mapped[str] = mapped_column(primary_key=True)
    label_hu: Mapped[str]
    label_en: Mapped[str]
    cost_type: Mapped[str]  # "labor", "material", "mixed"

class NotIncluded(Base):
    __tablename__ = "not_included"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id"))
    item_hu: Mapped[str]
    sort_order: Mapped[int]
    
    quote: Mapped["Quote"] = relationship(back_populates="not_included")

class BuyerPurchase(Base):
    __tablename__ = "buyer_purchases"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id"))
    item_hu: Mapped[str]
    sort_order: Mapped[int]
    
    quote: Mapped["Quote"] = relationship(back_populates="buyer_purchases")

class GeneralNote(Base):
    __tablename__ = "general_notes"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id"))
    note_hu: Mapped[str]
    sort_order: Mapped[int]
    
    quote: Mapped["Quote"] = relationship(back_populates="general_notes")

class QuoteVector(Base):
    __tablename__ = "quote_vectors"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id"))
    chroma_chunk_id: Mapped[str]   # links to ChromaDB document ID
    section: Mapped[str]
    version: Mapped[int]
    
    quote: Mapped["Quote"] = relationship(back_populates="vectors")

class CPIRecord(Base):
    __tablename__ = "cpi_records"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    year: Mapped[int]
    quarter: Mapped[int]
    component: Mapped[str]   # "materials" or "labor"
    index_value: Mapped[float]
    
    __table_args__ = (UniqueConstraint("year", "quarter", "component"),)

class Listing(Base):
    __tablename__ = "listings"

    source_site: Mapped[str] = mapped_column(primary_key=True)
    unit_id: Mapped[str] = mapped_column(primary_key=True)
    project_name: Mapped[str]
    district: Mapped[Optional[str]]
    rooms: Mapped[Optional[str]]
    area_m2: Mapped[Optional[float]]
    floor: Mapped[Optional[int]]
    completion_date: Mapped[Optional[str]]
    price_huf: Mapped[Optional[int]]
    price_per_m2: Mapped[Optional[int]]
    scraped_at: Mapped[str]
    url: Mapped[str]

SEED_WORK_CATEGORIES = [
    ("bontás",            "Bontás",                 "Demolition",             "labor"),
    ("víz_fűtés",         "Víz és fűtés",           "Plumbing/heating",       "mixed"),
    ("villany",           "Villanyszerelés",        "Electrical",             "labor"),
    ("klíma",             "Klíma",                  "AC",                     "labor"),
    ("vakolás",           "Vakolás",                "Plastering",             "mixed"),
    ("burkolás",          "Burkolás",               "Tiling",                 "mixed"),
    ("glettelés_festés",  "Glettelés és festés",    "Painting",               "mixed"),
    ("parketta",          "Parketta",               "Flooring",               "labor"),
    ("egyéb_köműves",     "Egyéb köműves",          "Misc masonry",           "mixed"),
    ("szállítás",         "Szállítás/segédmunka",   "Logistics",              "labor"),
    ("szigetelés",        "Szigetelés",             "Insulation",             "mixed"),
    ("nyílászáró",        "Nyílászáró csere",       "Windows/doors",          "mixed"),
    ("konyha",            "Konyhabútor",            "Kitchen cabinetry",      "mixed"),
    ("fürdő",             "Fürdőszoba",             "Bathroom",               "mixed"),
    ("fűtés_rendszer",    "Fűtésrendszer",          "Heating system",         "mixed"),
    ("gipszkarton",       "Gipszkarton/álmennyezet", "Drywall/suspended ceil.", "mixed"),
    ("egyéb",             "Egyéb",                  "Other",                  "mixed"),
]
