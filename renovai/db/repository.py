import uuid
from datetime import date, datetime
from typing import List, Optional, Dict, Any
from sqlalchemy import select, delete, func, and_, cast, Float
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import Quote, LineItemORM, WorkCategory, NotIncluded, BuyerPurchase, GeneralNote, QuoteVector, CPIRecord
from ..ingestion.models import RenovationQuote, LineItem as LineItemPydantic

class QuoteRepository:
    
    async def upsert_quote(
        self,
        session: AsyncSession,
        parsed_quote: RenovationQuote
    ) -> Quote:
        """Upserts a RenovationQuote into the database."""
        meta = parsed_quote.metadata
        
        # Check if already exists
        stmt = select(Quote).where(Quote.file_name == meta.file_name).options(
            selectinload(Quote.line_items),
            selectinload(Quote.not_included),
            selectinload(Quote.buyer_purchases),
            selectinload(Quote.general_notes)
        )
        res = await session.execute(stmt)
        db_quote = res.scalars().first()
        
        if db_quote:
            # Delete old related rows to simplify update
            # (Cascade delete-orphan in models will handle this when we clear collections)
            db_quote.line_items = []
            db_quote.not_included = []
            db_quote.buyer_purchases = []
            db_quote.general_notes = []
            # We keep vectors for now as they are registered separately
        else:
            db_quote = Quote(
                file_name=meta.file_name,
                line_items=[],
                not_included=[],
                buyer_purchases=[],
                general_notes=[],
                vectors=[]
            )
            session.add(db_quote)
            
        # Update metadata
        db_quote.address_raw = meta.address_raw
        db_quote.district = meta.district
        db_quote.quote_date = meta.quote_date
        db_quote.total_labor_huf = meta.total_labor
        db_quote.total_material_huf = meta.total_material
        db_quote.grand_total_huf = meta.grand_total
        db_quote.timeline_weeks_min = meta.timeline_weeks_min
        db_quote.timeline_weeks_max = meta.timeline_weeks_max
        db_quote.payment_schedule = meta.payment_schedule
        db_quote.num_line_items = len(parsed_quote.line_items)
        
        # Detect slag complication
        has_slag = False
        
        # Load work categories for keyword matching
        cat_stmt = select(WorkCategory)
        cat_res = await session.execute(cat_stmt)
        categories = cat_res.scalars().all()
        
        def assign_category(name: str) -> Optional[str]:
            name_lower = name.lower()
            for cat in categories:
                if name_lower.startswith(cat.label_hu.lower()):
                    return cat.key
            # Fallback keyword match
            keywords = {
                "bontás": "bontás",
                "víz": "víz_fűtés",
                "fűtés": "víz_fűtés",
                "villany": "villany",
                "klíma": "klíma",
                "vakolás": "vakolás",
                "burkolás": "burkolás",
                "festés": "glettelés_festés",
                "glett": "glettelés_festés",
                "parketta": "parketta",
                "kőműves": "egyéb_köműves",
                "szállítás": "szállítás",
                "anyagmozgatás": "szállítás"
            }
            for kw, key in keywords.items():
                if kw in name_lower:
                    return key
            return "egyéb"

        # Add line items
        for item in parsed_quote.line_items + parsed_quote.alternatives:
            if "kohósalak" in (item.notes or "").lower() or "kohósalak" in item.name.lower():
                has_slag = True
                
            orm_item = LineItemORM(
                name_hu=item.name,
                category_key=assign_category(item.name),
                labor_cost_huf=item.labor_cost,
                material_cost_huf=item.material_cost,
                total_cost_huf=item.total_cost,
                notes_hu=item.notes,
                section=item.section,
                version=item.version,
                is_included=True
            )
            db_quote.line_items.append(orm_item)
            
        # Add not included
        for i, item in enumerate(parsed_quote.not_included):
            db_quote.not_included.append(NotIncluded(item_hu=item, sort_order=i))
            
        # Add buyer purchases
        for i, item in enumerate(parsed_quote.buyer_purchases):
            db_quote.buyer_purchases.append(BuyerPurchase(item_hu=item, sort_order=i))
            
        # Add general notes
        for i, note in enumerate(parsed_quote.general_notes):
            if "kohósalak" in note.lower():
                has_slag = True
            db_quote.general_notes.append(GeneralNote(note_hu=note, sort_order=i))
            
        db_quote.has_slag_complication = has_slag
        
        await session.flush()
        return db_quote

    async def get_quote_by_file(self, session: AsyncSession, file_name: str) -> Optional[Quote]:
        stmt = select(Quote).where(Quote.file_name == file_name).options(
            selectinload(Quote.line_items),
            selectinload(Quote.not_included),
            selectinload(Quote.buyer_purchases),
            selectinload(Quote.general_notes)
        )
        res = await session.execute(stmt)
        return res.scalars().first()

    async def list_quotes(
        self,
        session: AsyncSession,
        district: Optional[int] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        has_slag: Optional[bool] = None,
    ) -> List[Quote]:
        stmt = select(Quote)
        filters = []
        if district is not None:
            filters.append(Quote.district == district)
        if date_from is not None:
            filters.append(Quote.quote_date >= date_from)
        if date_to is not None:
            filters.append(Quote.quote_date <= date_to)
        if has_slag is not None:
            filters.append(Quote.has_slag_complication == has_slag)
            
        if filters:
            stmt = stmt.where(and_(*filters))
            
        res = await session.execute(stmt)
        return res.scalars().all()

    async def get_cost_stats(
        self, session: AsyncSession, district: Optional[int] = None
    ) -> dict:
        filters = []
        if district is not None:
            filters.append(Quote.district == district)
            
        stmt = select(
            func.count(Quote.id),
            func.avg(Quote.grand_total_huf),
            func.min(Quote.grand_total_huf),
            func.max(Quote.grand_total_huf),
            func.avg(cast(Quote.total_labor_huf, Float) / cast(Quote.grand_total_huf, Float))
        )
        
        if filters:
            stmt = stmt.where(and_(*filters))
            
        res = await session.execute(stmt)
        count, avg_total, min_total, max_total, avg_labor_share = res.fetchone()
        
        return {
            "count": count or 0,
            "avg_total": int(avg_total) if avg_total else 0,
            "min_total": min_total or 0,
            "max_total": max_total or 0,
            "avg_labor_share": float(avg_labor_share) if avg_labor_share else 0.0
        }

    async def register_vector_chunk(
        self,
        session: AsyncSession,
        quote_id: uuid.UUID,
        chroma_chunk_id: str,
        section: str,
        version: int
    ) -> QuoteVector:
        vector = QuoteVector(
            quote_id=quote_id,
            chroma_chunk_id=chroma_chunk_id,
            section=section,
            version=version
        )
        session.add(vector)
        await session.flush()
        return vector

class CPIRepository:
    
    async def upsert_cpi_records(
        self, 
        session: AsyncSession, 
        records: List[Any], 
        component_override: Optional[str] = None
    ) -> int:
        """Upserts CPI records. component_override can specify 'materials' or 'labor' if not on objects."""
        count = 0
        for rec in records:
            comp = component_override or getattr(rec, 'component', None)
            if not comp:
                raise AttributeError("CPI record missing 'component' attribute and no override provided.")
                
            stmt = select(CPIRecord).where(and_(
                CPIRecord.year == rec.year,
                CPIRecord.quarter == rec.quarter,
                CPIRecord.component == comp
            ))
            res = await session.execute(stmt)
            db_rec = res.scalars().first()
            
            if db_rec:
                db_rec.index_value = rec.index_value
            else:
                db_rec = CPIRecord(
                    year=rec.year,
                    quarter=rec.quarter,
                    component=comp,
                    index_value=rec.index_value
                )
                session.add(db_rec)
            count += 1
        await session.flush()
        return count

    async def get_index_series(self, session: AsyncSession, component: str) -> List[CPIRecord]:
        stmt = select(CPIRecord).where(CPIRecord.component == component).order_by(CPIRecord.year, CPIRecord.quarter)
        res = await session.execute(stmt)
        return res.scalars().all()
