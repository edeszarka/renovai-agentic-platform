---
name: finishing-specialist
description: >
  Handle all surface finishing and interior fit-out for Hungarian apartment
  renovations: tiling (burkolás) with cost variations by tile size and quality
  tier, wall surface preparation including sawdust wallpaper removal and lime
  layer treatment, painting and plastering with paint quantity estimation,
  laminate flooring installation with XPS underlayment, suspended ceilings
  (gipszkarton álmennyezet), dispersion-based waterproofing for shower trays
  and bathtubs, and interior door selection by hinge type and configuration.
  This skill owns all per-square-meter unit cost calculations and quality tier
  classification. It does NOT handle structural work, demolition, or MEP.
trigger_conditions:
  - User asks about tiling costs by tile size or quality category
  - User asks about wall surface preparation (tapéta eltávolítás, csiszolás,
    glettelés, festés, festék mennyiség)
  - User asks about flooring (laminált padló, vinyl, XPS, párazáró fólia)
  - User asks about waterproofing type for shower tray or bathtub
  - User asks about interior doors by hinge type or configuration
  - User asks about suspended ceiling costs
  - Hungarian keywords: "burkolás", "csempe", "glettelés", "festés",
    "tapéta", "laminált", "vízszigetelés", "beltéri ajtó",
    "gipszkarton álmennyezet", "szegőléc", "fugázás"
not_trigger_when:
  - User asks about structural wall construction or floor reinforcement
  - User asks about demolition debris removal or tonnage
  - User asks about cement-based waterproofing for built-in shower
  - User asks about MEP work (delegate to MEP agent)
depends_on:
  - .agent/skills/finishing-specialist/references/pricing.md
calls:
  static_data: .agent/skills/finishing-specialist/references/pricing.md
---

## Workflow

### Step 1: Classify the finishing scenario
Determine which surface type and condition the user is asking about.
- **Tiling**: ask for tile size (60x30, 60x60, 60x120, etc.) and quality tier
- **Wall prep**: ask if wallpaper is present (especially fűrészporos tapéta)
- **Flooring**: ask for laminate thickness (7-12mm) and area
- **Waterproofing**: ask if built-in shower or shower tray/bathtub
- **Doors**: ask for hinge type (külső/belső zsanéros), width, glass

### Step 2: Select the pricing reference
Load `references/pricing.md` and select the relevant table. Do not load the full file if only one table is needed.

### Step 3: Compute the estimate
1. Match user parameters to the closest reference row
2. For tiling: multiply per-sqm rate by total area, add category multiplier
3. For wall prep: add surcharge if sawdust wallpaper is present
4. For doors: multiply per-unit rate by quantity
5. Return low/mid/high range with inflation date

### Step 4: Append finishing notes
- If sawdust wallpaper: MUST include "tapéta kaparás szükséges a csiszolás előtt"
- If tile size > 60x60: note that larger tiles require more precision
- If Q3 plastering: specify quality level in estimate

## Edge Cases

| Condition | Handling |
|-----------|----------|
| Tile size not provided | Default to 60x60 (most common), flag as assumption |
| Wall condition unknown | Ask whether wallpaper is present before quoting |
| Door type not specified | Default to külső zsanéros, CPL fóliás, üveg nélkül |
| Quality tier not specified | Default to alsó középkategória, flag as assumption |
