---
name: construction-planner
description: >
  Construction Planner — technical sequencing and cascading cost logic for
  renovation projects. Enforces correct construction order and computes
  "Hidden Technological Chains" where one work item triggers mandatory
  downstream phases (e.g. floor work in pre-1960 buildings triggers the full
  Slag Chain). All estimates in HUF.
trigger_conditions:
  - Buyer asks for a renovation plan, sequence, or step-by-step breakdown
  - Buyer asks about construction order: "mit kell előbb csinálni"
  - Buyer asks for a complete renovation schedule including all phases
  - Buyer asks about technical feasibility of specific work items
  - Buyer selects "Teljes felújítás" (Full renovation) or "Részleges" (Partial)
not_trigger_when:
  - Buyer asks only about building condition or red flags (use expert-interviewer)
  - Buyer asks only for a price estimate without sequencing (use cost_estimate)
  - Buyer asks about market trends (use market_data_query)
depends_on:
  - .agent/skills/construction-planner/references/sequencing_rules.md
  - .agent/skills/masonry-specialist/references/pricing.md
  - .agent/skills/finishing-specialist/references/pricing.md
calls:
  static_data: .agent/skills/construction-planner/references/sequencing_rules.md
  mcp_tool: estimate_renovation_cost
---

## Instructions (Load after Metadata)

### Cascading Dependency Logic — "Hidden Technological Chains"

The Construction Planner MUST detect and aggregate ALL phases triggered by a
single work item. This is the core anti-underestimation mechanism.

| Trigger Condition | Activated Chain | Mandatory Phases |
|------------------|----------------|------------------|
| Floor work in pre-1960 building | **Kohósalak lánc (Slag Chain)** | Salak eltávolítás → EPS szigetelés → Betonozás és szintezés |
| Fűrészporos tapéta detected | **Tapéta lánc (Wallpaper Chain)** | Kaparás → Q3 vakolás → Glettelés |
| Teljes felújítás (Full) | **Infrastrukturális minimumok** | Elektromos szabványosítás (300k) + Gáz/fűtés alap (800k) + Logisztikai pótlék (15% labor) |

### Mandatory Sequencing Rules

The Construction Planner MUST enforce the following order. Any deviation
MUST produce a warning.

| Step | Phase | Work Items | Cascading Trigger |
|------|-------|------------|-------------------|
| 0 | Site Prep | **Salak lánc**: eltávolítás + EPS + betonozás (if pre-1960 slab + floor work) | Floor work in pre-1960 |
| 1 | Bontás (Demolition) | Wall removal, floor tile removal, fixture removal | — |
| 2 | Kőműves (Masonry) | New walls, floor reinforcement, door openings | Pre-1920: födém megerősítés |
| 3 | Gépészet (Rough-in) | Electrical wiring, plumbing pipes, HVAC ducts | — |
| 4 | Vakolás & Glettelés | Plastering, sanding, wallpaper scraping if needed | Fűrészporos tapéta → kaparás |
| 5 | Burkolás (Flooring) | Tile, laminate, parquet, bathroom waterproofing | Épített zuhany → cement szigetelés |
| 6 | Festés & Szerelés | Painting, fixtures, switches, sockets, doors | — |
| I1 | Infra: Electrical Std | Electrical standardization minimum 300k Ft | Full renovation ONLY |
| I2 | Infra: Gas/Heating | Chimney + design baseline 800k Ft | Full renovation ONLY |
| I3 | Infra: Logistics | Sitt removal + protection, 15% of total labor | Full renovation ONLY |

### Cost Itemization Rules — Exact HUF Values (Source 5)

Each step MUST use these exact HUF ranges. Do NOT scale arbitrarily.

| Phase | Material Cost Range | Labor Cost Range | Notes |
|-------|-------------------|------------------|-------|
| **Slag Chain** (aggregated) | EPS 700k-850k + Beton 6-9k/nm | 1 300 000 - 2 000 000 Ft | Pre-1960 + floor work. THREE sub-items mandatory. |
| Bontás (Demolition) | 0 - 50 000 Ft | 1 000 - 2 000 Ft/nm | Depends on scope |
| Kőműves (Masonry) | 249 000 - 402 000 Ft | 280 000 - 420 000 Ft | Pre-1920: 402k material + 320-420k labor |
| Electrical standardisation | 300 000 Ft (fixed) | 0 Ft (in labor) | Full renovation ONLY |
| Gas/Heating baseline | 800 000 Ft (fixed) | 0 Ft (in labor) | Full renovation ONLY |
| Logistics surcharge | 0 Ft | 15% of total labor | Full renovation ONLY |
| Gépészet (Rough-in) | 200 000 - 500 000 Ft | 500 000 - 1 000 000 Ft | Electrical + plumbing |
| Vakolás (Plaster) | 80 000 - 180 000 Ft | 180 000 - 380 000 Ft | Add scraping surcharge +180-280k labor if wallpaper |
| Burkolás (Flooring) | 130 000 - 250 000 Ft | 300 000 - 600 000 Ft | Cement szigetelés +130k if épített zuhany |
| Festés (Painting) | 50 000 - 100 000 Ft | 200 000 - 400 000 Ft | Final fixtures included |

### Quality Gates & Vibe Diff Mandate

1. Every plan MUST follow the mandatory sequencing order with cascading logic
2. Every phase MUST include itemized material + labor cost ranges in HUF
3. **MANDATORY**: Every Tab 2 plan MUST include a Hungarian Vibe Diff
4. If total estimate > 10M HUF, Vibe Diff MUST list ALL "Hidden Technological Chains"
5. Slag Chain MUST be a single aggregated block with 3 sub-items, never 1 line
6. Wallpaper surcharge: labor +180 000 - 280 000 Ft (exact Source 5 values)
7. Infrastructure minimums MUST be appended as separate phases for Full renovation
8. Every estimate MUST use exact HUF values from the Cost Itemization table
9. Confidence score: 0.85 base, 0.80 if cascading chains are active

## Scripts (Load on Demand)

### Sequencing Method

```text
1. Collect renovation scope from user input (demolition, masonry, rough-in, finishing)
2. Determine building era — if pre-1960 AND floor work: ACTIVATE Slag Chain
3. Determine wall condition — if fűrészporos tapéta: ACTIVATE Wallpaper Chain
4. Determine shower type — if épített zuhany: cement waterproofing
5. Build step sequence based on Mandatory Sequencing Rules
6. For Teljes felújítás: append Infrastructure Minimums (Elec 300k, Gas 800k, Logistics 15%)
7. For each step:
   a. Look up EXACT cost range from Cost Itemization table (Source 5)
   b. Apply area scaling only where noted (betonozás, demolition per sqm)
   c. Aggregate cascading chains into single blocks with sub-items
8. Generate mandatory Hungarian Vibe Diff with Hidden Chains if total > 10M
9. Return complete plan with all steps, costs, warnings, and Vibe Diff
```
