import joblib
import pandas as pd
import numpy as np
import logging
import os
from pathlib import Path
from datetime import date
from typing import List, Optional, Tuple, Dict, Any
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error
from sklearn.model_selection import LeaveOneOut
from dotenv import load_dotenv

# Load .env to ensure TABPFN_TOKEN is in os.environ
load_dotenv()

try:
    from tabpfn import TabPFNRegressor
    TABPFN_AVAILABLE = True
except ImportError:
    TABPFN_AVAILABLE = False

from .feature_extractor import QuoteFeatures, extract_features
from ..ingestion.inflation_models import PriceIndex
from ..ingestion.inflation_calc import get_factor

logger = logging.getLogger(__name__)

MODEL_FEATURES = [
    "district",
    "total_area_sqm",
    "num_rooms",
    "has_plumbing_work",
    "has_electrical_work",
    "has_flooring_work",
    "has_demolition",
    "has_slag_complication",
    "num_line_items",
    "labor_to_material_ratio",
    "demolition_cost_share",
    "plumbing_cost_share"
]

def train_model(X: pd.DataFrame, y: pd.Series, model_dir: Path) -> dict:
    """Trains GBR, Ridge, and TabPFN models, selects the best using LOO-CV, and saves them."""
    X = X[MODEL_FEATURES].copy()
    
    # Pre-handle entirely empty columns that median imputer can't handle
    for col in X.columns:
        if X[col].isnull().all():
            logger.warning(f"Feature '{col}' is entirely empty in training data. Filling with 0.")
            X[col] = 0.0

    loo = LeaveOneOut()
    
    def evaluate_model(model_instance):
        y_true = []
        y_pred = []
        
        # Check if model is TabPFN to avoid Pipeline if it handles scaling/imputing internally
        is_tabpfn = TABPFN_AVAILABLE and isinstance(model_instance, TabPFNRegressor)
        
        for train_index, test_index in loo.split(X):
            X_train, X_test = X.iloc[train_index], X.iloc[test_index]
            y_train, y_test = y.iloc[train_index], y.iloc[test_index]
            
            if is_tabpfn:
                # TabPFN handles missing values natively, but we ensure no NaNs remain
                # just in case for older versions
                X_train_filled = X_train.fillna(0)
                X_test_filled = X_test.fillna(0)
                model_instance.fit(X_train_filled, y_train)
                y_pred_val = model_instance.predict(X_test_filled)[0]
            else:
                pipe = Pipeline([
                    ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                    ("regressor", model_instance)
                ])
                pipe.fit(X_train, y_train)
                y_pred_val = pipe.predict(X_test)[0]
                
            y_true.append(y_test.iloc[0])
            y_pred.append(y_pred_val)
            
        mae = mean_absolute_error(y_true, y_pred)
        # Avoid MAPE error on 0 actuals
        y_true_np = np.array(y_true)
        y_pred_np = np.array(y_pred)
        mask = y_true_np != 0
        if np.any(mask):
            mape = mean_absolute_percentage_error(y_true_np[mask], y_pred_np[mask])
        else:
            mape = 1.0 # Fallback
            
        return mae, mape, y_true, y_pred

    results = {}

    # GBR
    gbr = GradientBoostingRegressor(n_estimators=200, max_depth=4, learning_rate=0.05, random_state=42)
    results["gbr"] = evaluate_model(gbr)
    
    # Ridge
    ridge = Ridge(alpha=1.0)
    results["ridge"] = evaluate_model(ridge)
    
    # TabPFN
    if TABPFN_AVAILABLE:
        try:
            # Check for GPU
            device = "cpu"
            import torch
            if torch.cuda.is_available():
                device = "cuda"
            
            # Ensure TABPFN_TOKEN is explicitly set from os.environ
            token = os.environ.get("TABPFN_TOKEN")
            if not token:
                logger.warning("TABPFN_TOKEN not found in environment. TabPFN might trigger interactive login.")

            tabpfn = TabPFNRegressor(device=device)
            results["tabpfn"] = evaluate_model(tabpfn)
            logger.info(f"TabPFN-2.5 evaluated on {device}.")
        except Exception as e:
            logger.warning(f"Failed to evaluate TabPFN: {e}")

    # Selection logic: prioritize TabPFN if its MAPE is comparable or better
    recommended = "gbr"
    if results["ridge"][1] < results["gbr"][1] - 0.10:
        recommended = "ridge"
    
    if "tabpfn" in results:
        # TabPFN is often very strong on small data, we give it high priority
        if results["tabpfn"][1] <= results[recommended][1] + 0.05:
            recommended = "tabpfn"

    best_mae, best_mape, best_true, best_pred = results[recommended]
    
    # Final fit on all data
    if recommended == "tabpfn":
        device = "cpu"
        import torch
        if torch.cuda.is_available(): device = "cuda"
        final_model = TabPFNRegressor(device=device)
        final_model.fit(X.fillna(0), y)
    else:
        best_instance = gbr if recommended == "gbr" else ridge
        final_model = Pipeline([
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("regressor", best_instance)
        ])
        final_model.fit(X, y)
    
    # Save
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(final_model, model_dir / "price_model.joblib")
    
    # Feature importances (only for GBR)
    importances = {}
    if recommended == "gbr":
        feat_imp = final_model.named_steps["regressor"].feature_importances_
        # GBR in pipeline might have indicator features, we just take first 12
        importances = dict(zip(MODEL_FEATURES, feat_imp.tolist()[:len(MODEL_FEATURES)]))
        importances = dict(sorted(importances.items(), key=lambda x: x[1], reverse=True)[:8])

    return {
        "loo_mae": float(best_mae),
        "loo_mape_pct": float(best_mape * 100),
        "n_samples": len(y),
        "feature_importances": importances,
        "recommended_model": recommended,
        "mape_comparison": {k: v[1] for k, v in results.items()},
        "examples": [{"actual": t, "pred": p} for t, p in zip(best_true[:3], best_pred[:3])]
    }

def predict(
    features: QuoteFeatures,
    price_index: PriceIndex,
    target_date: date,
    model_dir: Path
) -> dict:
    """Predicts renovation cost range with inflation adjustment."""
    model_path = model_dir / "price_model.joblib"
    if not model_path.exists():
        raise FileNotFoundError(f"Model not found at {model_path}")
        
    model = joblib.load(model_path)
    
    # Prepare input
    input_df = pd.DataFrame([features.model_dump(include=set(MODEL_FEATURES))])
    
    # Point estimate for baseline
    # Handle TabPFN specific output if it were to return distribution, 
    # but standard predict() returns mean/median point estimate.
    point_estimate = model.predict(input_df)[0]
    
    # Identify model type for reporting
    model_name = "unknown"
    if TABPFN_AVAILABLE and isinstance(model, TabPFNRegressor):
        model_name = "tabpfn-2.5"
    elif isinstance(model, Pipeline):
        reg = model.named_steps["regressor"]
        if isinstance(reg, GradientBoostingRegressor): model_name = "gbr"
        elif isinstance(reg, Ridge): model_name = "ridge"
    
    # Inflation adjustment
    baseline_date = date(2024, 2, 15) # Mid-Q1 2024
    f_labor = get_factor(price_index, "labor", baseline_date, target_date)
    f_material = get_factor(price_index, "materials", baseline_date, target_date)
    
    # Split by ratio
    ratio = features.labor_to_material_ratio
    m_base = point_estimate / (ratio + 1)
    l_base = point_estimate - m_base
    
    point_adj = (l_base * f_labor) + (m_base * f_material)
    
    return {
        "estimate_low_huf": int(point_adj * 0.85),
        "estimate_mid_huf": int(point_adj),
        "estimate_high_huf": int(point_adj * 1.20),
        "inflation_adjusted_to": target_date.isoformat(),
        "inflation_factor_labor": round(f_labor, 3),
        "inflation_factor_materials": round(f_material, 3),
        "model_used": model_name,
        "warning": None
    }

def find_similar_quotes(
    features: QuoteFeatures,
    quotes_dir: Path,
    top_k: int = 3
) -> List[dict]:
    """Finds top_k similar historical quotes using Euclidean distance on numeric features."""
    all_feats = []
    
    num_cols = ["district", "num_line_items", "labor_to_material_ratio", "demolition_cost_share", "plumbing_cost_share"]
    
    target_vec = np.array([getattr(features, col) for col in num_cols])
    
    for file in quotes_dir.glob("*_adjusted.json"):
        try:
            f = extract_features(file)
            vec = np.array([getattr(f, col) for col in num_cols])
            dist = np.linalg.norm(target_vec - vec)
            all_feats.append({
                "file": file.name,
                "address": f.district, # Should really have address but QuoteFeatures only has district
                "grand_total_adjusted": f.grand_total_adjusted,
                "distance": dist
            })
        except Exception:
            continue
            
    all_feats.sort(key=lambda x: x["distance"])
    return all_feats[:top_k]
