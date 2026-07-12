# Inflation Calculation Audit — 2026-07-12

Audit of the CPI-based inflation adjustment in `renovai/ingestion/inflation_calc.py`.

---

## TASK 1: CPI Data Source

**Files:** `data/raw/inflation/materials_cpi.csv` and `data/raw/inflation/labor_cpi.csv`

### What index is this?

The code comment (`load_price_index`) says *"Loads KSH CPI data from CSV files"*.
**The values are synthetic/placeholder data, not real KSH statistics.**

Evidence:
- Both indices start at exactly 100.0 in 2021-Q1 (clean base-100 reset)
- The quarterly increments are suspiciously round (materials: +5/quarter in 2021, +10/quarter in 2022, +5→+3→+2→+1 tapering in 2023-24)
- Real KSH construction cost index (ÉVOSZ építési költségindex) would show different magnitudes — Hungarian construction inflation in 2022 was ~20-30% YoY, not a smooth linear ramp
- General KSH CPI and construction-specific CPI diverge significantly in Hungary (e.g. 2023 general CPI was ~17% but construction cost inflation was ~8-12%)

**Known accuracy issue:** These are not real CPI figures. Any estimate produced by the pipeline inherits this limitation. The compounding *math* is correct (see Task 2), but the *inputs* are fabricated.

### Time granularity and coverage

| Field | Value |
|-------|-------|
| Granularity | **Quarterly** (Q1–Q4 per year) |
| Coverage | **2021-Q1 through 2024-Q4** (16 data points each) |
| Base period | 2024-Q1 (stated in `PriceIndex.base_year/base_quarter`) |
| Latest data | **2024-Q4** |
| Data gap | **~18 months** — data ends 2024-Q4, today is 2026-07-12 |

### Raw data

**Materials_cpi.csv:**
```
2021 Q1: 100.0   Q2: 105.0   Q3: 110.0   Q4: 115.0
2022 Q1: 125.0   Q2: 135.0   Q3: 145.0   Q4: 155.0
2023 Q1: 170.0   Q2: 175.0   Q3: 178.0   Q4: 180.0
2024 Q1: 182.0   Q2: 184.0   Q3: 185.0   Q4: 186.0
```

**Labor_cpi.csv:**
```
2021 Q1: 100.0   Q2: 102.0   Q3: 104.0   Q4: 106.0
2022 Q1: 112.0   Q2: 115.0   Q3: 118.0   Q4: 121.0
2023 Q1: 135.0   Q2: 140.0   Q3: 145.0   Q4: 150.0
2024 Q1: 155.0   Q2: 158.0   Q3: 160.0   Q4: 162.0
```

---

## TASK 2: get_factor() Formula

### Formula

```python
factor = interpolated_index[to_date] / interpolated_index[from_date]
```

Where `interpolated_index(date)` is **linear interpolation** between the two
bracketing quarterly data points:

```
score = year + (day_of_year - 1) / days_in_year
index = v1 + (score - q1_score) * (v2 - v1) / (q2_score - q1_score)
```

This is **not** discrete year-over-year compounding (e.g. `1.20 * 1.15 * 1.10`).
Instead it computes a **ratio between two points on a continuous cumulative index**.

### Why this is correct (for a cumulative index)

The index values are cumulative — each quarterly value represents the total
change since the base period. Taking `index(to) / index(from)` implicitly
compounds all intermediate period changes. This is mathematically equivalent
to multiplying discrete YoY factors:

```
index(2024-Q4) / index(2023-Q1)
  = [index(2024-Q4) / index(2023-Q4)] × [index(2023-Q4) / index(2023-Q1)]
```

### Worked example

**Quote:** 2023-07-01, adjusting to baseline 2024-02-15

| Step | Value |
|------|-------|
| `from_date` score (2023-07-01) | 2023.4959 |
| Bracketed by | Q2 2023 (2023.375) and Q3 2023 (2023.625) |
| Interpolation fraction `t` | (2023.4959 - 2023.375) / 0.25 = 0.4836 |
| Materials from | 175.0 + 0.4836 × 3.0 = **176.4507** |
| Labor from | 140.0 + 0.4836 × 5.0 = **142.4178** |
| | |
| `to_date` score (2024-02-15) | 2024.1230 |
| Bracketed by | Q4 2023 (2023.875) and Q1 2024 (2024.125) |
| Interpolation fraction `t` | (2024.1230 - 2023.875) / 0.25 = 0.9918 |
| Materials to | 180.0 + 0.9918 × 2.0 = **181.9836** |
| Labor to | 150.0 + 0.9918 × 5.0 = **154.9590** |
| | |
| **Materials factor** | 181.9836 / 176.4507 = **1.031357** |
| **Labor factor** | 154.9590 / 142.4178 = **1.088059** |

Code output matches manual calculation to 6 decimal places.

### Two-step pipeline equivalence

The pipeline has two stages:
1. **Ingestion:** `adjust_quote(quote, pi, TARGET_DATE=2026-07-10)` — adjusts quote_date → 2026-07-10
2. **Estimation:** `get_factor(pi, type, baseline=2024-02-15, today)` — adjusts baseline → today

These are **not** applied sequentially to the same amount. Ingestion stores
`grand_total_adjusted` as a one-shot adjustment from quote_date to TARGET_DATE.
The estimation pipeline uses `scope_matched_estimate()` which works on
per-sqm averages from the *already-adjusted* corpus, then applies a separate
baseline→today factor.

The one-shot and two-step approaches produce **identical factors** because:

```
index(today) / index(quote_date)
  = [index(today) / index(baseline)] × [index(baseline) / index(quote_date)]
```

Verified: product of 2-step factors equals 1-step factor to 6 decimal places.

---

## TASK 3: Manual vs Code Comparison

### Quote: "komplett, 1900-as évek, van salak, 80-90nm"

| Field | Value |
|-------|-------|
| Quote date | 2023-07-01 |
| Grand total | 38,096,798 HUF |
| Area | 90 m² |
| Line items | 42 |
| Actual labor share | **85.1%** (32.4M labor / 38.1M total) |

### Factor computation

| Target | Materials factor | Labor factor |
|--------|-----------------|--------------|
| Baseline 2024-02-15 | 1.031357 | 1.088059 |
| Today 2026-07-12 | 1.054119 | 1.137498 |
| **Note:** both are clamped to 2024-Q4 values (data doesn't extend further) | | |

### Code output vs manual

Using `adjust_quote(rq, pi, TARGET_DATE=date(2026, 7, 10))` (matching the ingestion script):

| Metric | Value |
|--------|-------|
| Code output (`adjust_quote`) | **43,020,880** |
| DB stored value | **43,020,880** |
| Difference | **0** (exact match) |
| Inflation delta | +12.9% |

The DB and code agree because the DB was populated by the same `adjust_quote()` function
with `TARGET_DATE = date(2026, 7, 10)` (set in `scripts/ingest_2023_2024_quotes.py:42`).

### 55/45 split comparison

The estimation pipeline uses a 55/45 labor/materials split on the *aggregate*
total, but `adjust_quote()` applies factors *per line item* using actual
labor/material breakdowns. For this quote (85.1% labor), the difference is
significant:

| Method | Adjusted total | vs original |
|--------|---------------|-------------|
| Per-item (actual 85.1% labor) | 43,020,880 | +12.9% |
| 55/45 aggregate split | 41,905,620 | +10.0% |
| Difference | **1,115,260** | **2.9 pp** |

The 55/45 split **understates** inflation for labor-heavy quotes (like this one)
and **overstates** it for material-heavy quotes. The per-item method is more accurate.

---

## TASK 4: Forward Extrapolation

### What happens when CPI data ends before the target date

**The code silently clamps to the last available CPI value.**

From `inflation_calc.py:80-85`:
```python
last_score = _get_q_score(sorted_records[-1].year, sorted_records[-1].quarter)
if target_score >= last_score:
    return sorted_records[-1].index_value  # ← hardcoded to 2024-Q4
```

### Concrete example

| Target date | Materials factor (from baseline) | Behavior |
|-------------|--------------------------------|----------|
| 2024-Q4 | 1.022070 | interpolated (last data point) |
| 2025-Q1 | **1.022070** | **STALE** (clamped) |
| 2025-Q4 | **1.022070** | **STALE** (clamped) |
| 2026-01-01 | **1.022070** | **STALE** (clamped) |
| **2026-07-12 (today)** | **1.022070** | **STALE** (clamped) |
| 2027-01-01 | **1.022070** | **STALE** (clamped) |
| 2030-01-01 | **1.022070** | **STALE** (clamped) |

### Impact

**All inflation from 2025 onward is completely ignored.** Every estimate produced
by the pipeline for a 2026 target date is understated by approximately the
missing ~10-12% of cumulative inflation (real Hungarian construction CPI
added ~5-6% in 2025 and is tracking ~4-5% in 2026).

The code does NOT:
- Extrapolate forward from the last data point
- Log a warning when the target exceeds the data range (the warning was commented out at line 83)
- Return an error or special sentinel value

---

## Summary of Findings

| Issue | Severity | Description |
|-------|----------|-------------|
| Synthetic CPI data | **High** | Values are fabricated, not real KSH/ÉVOSZ data. All estimates inherit this inaccuracy. |
| Forward extrapolation gap | **High** | CPI data ends 2024-Q4; target dates in 2025+ are clamped, ignoring ~10-12% real inflation. |
| 55/45 split vs per-item | **Medium** | Aggregate 55/45 split diverges from per-item adjustment when actual labor share differs significantly (e.g. 85% labor quote has 2.9pp error). |
| Formula correctness | **OK** | `get_factor()` correctly computes ratio of interpolated index values. Two-step pipeline is equivalent to one-step. |
| Code-DB consistency | **OK** | `adjust_quote()` output matches stored DB values exactly (both use same TARGET_DATE and function). |
