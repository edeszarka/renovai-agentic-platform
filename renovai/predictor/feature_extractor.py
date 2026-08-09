import json
import pandas as pd
import numpy as np
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional, Tuple, Dict, Any
from ..ingestion.inflation_models import AdjustedQuote

WORK_CATEGORIES = {
    "bontás": "demolition",
    "víz_fűtés": "plumbing_heating",
    "villany": "electrical",
    "vakolás": "plastering",
    "burkolás": "tiling",
    "glettelés_festés": "painting_plastering",
    "parketta": "flooring",
    "egyéb_köműves": "misc_masonry",
    "szállítás_segédmunka": "logistics",
    "szigetelés": "insulation",
    "nyílászáró": "windows_doors",
    "konyha": "kitchen",
    "fürdő": "bathroom",
    "fűtés_rendszer": "heating",
    "gipszkarton": "drywall",
    "klíma": "ac",
}

CATEGORY_KEYWORDS = {
    "demolition": ["bontás", "sitt", "konténer", "vésés", "leverés"],
    "plumbing_heating": ["víz", "gáz", "fűtés", "csatorna", "szerelvényezés", "radiátor", "kazán", "kád", "wc", "mosdó", "csap"],
    "electrical": ["villany", "elektromos", "vezeték", "kapcsoló", "dugalj", "biztosíték", "fi-relé", "áram"],
    "plastering": ["vakolás", "falazás", "dryvit", "hálózás", "vakolat"],
    "tiling": ["burkolás", "csempe", "lap", "fuga", "aljzatkiegyenlítő", "járólap"],
    "painting_plastering": ["glettelés", "festés", "mázolás", "tapétázás", "alapozás"],
    "flooring": ["parketta", "szegély", "alátét", "csiszolás", "lakkozás", "padló", "laminált"],
    "misc_masonry": ["kőműves", "gipszkarton", "profil", "fal", "áthelyezés"],
    "logistics": ["szállítás", "cipelés", "anyagmozgatás", "segédmunka", "rakodás"],
    "insulation": ["szigetelés", "hungarocell", "EPS", "ásványgyapot", "rockwool", "hőszigetelés", "hangszigetelés"],
    "windows_doors": ["nyílászáró", "ablak", "ajtó", "tok", "spaletta", "redőny", "kilincs"],
    "kitchen": ["konyha", "konyhabútor", "szekrény", "munkalap", "mosogató", "csaptelep", "konyhaszekrény"],
    "bathroom": ["fürdő", "fürdőszoba", "wc", "kád", "zuhany", "tálca", "mosdó"],
    "heating": ["fűtésrendszer", "kazán", "kombi", "cirkó", "konvektor", "radiátor", "padlófűtés", "hőszivattyú", "gázkészülék"],
    "drywall": ["gipszkarton", "álmennyezet", "álmennyezet", "akusztikai", "cd profil", "vázszerkezet"],
}

class QuoteFeatures(BaseModel):
    # Apartment characteristics
    district: int
    total_area_sqm: Optional[float] = None
    num_rooms: Optional[int] = None
    building_era: Optional[int] = None

    # Derived scope features
    has_plumbing_work: bool
    has_electrical_work: bool
    has_flooring_work: bool
    has_demolition: bool
    has_slag_complication: bool
    num_line_items: int
    num_versions: int

    # Ratios
    labor_to_material_ratio: float
    demolition_cost_share: float
    plumbing_cost_share: float

    # Target
    grand_total_adjusted: Optional[int] = None

class ApartmentInput(BaseModel):
    district: int
    total_area_sqm: float
    num_rooms: int
    building_era: Optional[int] = None
    
    # Renovation scope
    needs_plumbing: bool = True
    needs_electrical: bool = False
    needs_flooring: bool = True
    needs_full_demolition: bool = True
    needs_windows_doors: bool = False
    needs_insulation: bool = False
    needs_ac: bool = False
    suspected_slag: bool = False

def extract_features(quote_json_path: Path) -> QuoteFeatures:
    """Extracts ML features from an inflation-adjusted quote JSON."""
    with open(quote_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        quote = AdjustedQuote.model_validate(data)
    
    meta = quote.original_metadata
    all_items = quote.line_items_adjusted
    
    cat_costs = {cat: 0 for cat in CATEGORY_KEYWORDS.keys()}
    cat_costs["other"] = 0
    
    has_plumbing = False
    has_electrical = False
    has_flooring = False
    has_demolition = False
    has_slag = False
    
    for adj_item in all_items:
        item = adj_item.original
        name_lower = item.name.lower()
        notes_lower = (item.notes or "").lower()
        
        if "kohósalak" in notes_lower or "kohósalak" in name_lower:
            has_slag = True
            
        matched = False
        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(k in name_lower for k in keywords):
                cat_costs[cat] += adj_item.total_cost_adjusted
                matched = True
                if cat == "plumbing_heating": has_plumbing = True
                if cat == "electrical": has_electrical = True
                if cat == "flooring" or cat == "tiling": has_flooring = True
                if cat == "demolition": has_demolition = True
        
        if not matched:
            cat_costs["other"] += adj_item.total_cost_adjusted

    grand_total = quote.grand_total_adjusted
    
    # Count unique versions
    versions = set(item.original.version for item in all_items)
    num_versions = len(versions)
    
    total_labor = sum(item.labor_cost_adjusted or 0 for item in all_items)
    total_material = sum(item.material_cost_adjusted or 0 for item in all_items)
    ratio = total_labor / total_material if total_material > 0 else 0
    
    return QuoteFeatures(
        district=meta.district or 0,
        has_plumbing_work=has_plumbing,
        has_electrical_work=has_electrical,
        has_flooring_work=has_flooring,
        has_demolition=has_demolition,
        has_slag_complication=has_slag,
        num_line_items=len(all_items),
        num_versions=num_versions,
        labor_to_material_ratio=ratio,
        demolition_cost_share=cat_costs["demolition"] / grand_total if grand_total > 0 else 0,
        plumbing_cost_share=cat_costs["plumbing_heating"] / grand_total if grand_total > 0 else 0,
        grand_total_adjusted=grand_total
    )

def build_feature_matrix(quotes_dir: Path) -> Tuple[pd.DataFrame, pd.Series]:
    """Loads all *_adjusted.json files and builds X, y for training."""
    features_list = []
    targets = []
    
    for file in quotes_dir.glob("*_adjusted.json"):
        try:
            feat = extract_features(file)
            features_list.append(feat.model_dump(exclude={"grand_total_adjusted"}))
            targets.append(feat.grand_total_adjusted)
        except Exception as e:
            print(f"Warning: Failed to process {file.name}: {e}")
            
    X = pd.DataFrame(features_list)
    y = pd.Series(targets)
    return X, y

def apartment_input_to_features(inp: ApartmentInput) -> QuoteFeatures:
    """Converts user apartment input into QuoteFeatures for prediction."""
    # Heuristic: base line items + 2 per scope flag
    num_items = 5
    if inp.needs_plumbing: num_items += 2
    if inp.needs_electrical: num_items += 2
    if inp.needs_flooring: num_items += 2
    if inp.needs_full_demolition: num_items += 2
    
    return QuoteFeatures(
        district=inp.district,
        total_area_sqm=inp.total_area_sqm,
        num_rooms=inp.num_rooms,
        building_era=inp.building_era,
        has_plumbing_work=inp.needs_plumbing,
        has_electrical_work=inp.needs_electrical,
        has_flooring_work=inp.needs_flooring,
        has_demolition=inp.needs_full_demolition,
        has_slag_complication=inp.suspected_slag,
        num_line_items=num_items,
        num_versions=1,
        labor_to_material_ratio=1.2, # heuristic typical average
        demolition_cost_share=0.15 if inp.needs_full_demolition else 0.0,
        plumbing_cost_share=0.25 if inp.needs_plumbing else 0.0
    )
