---
name: construction-planner
description: >
  Construction Planner — technical sequencing and cost logic for renovation projects.
  Enforces correct construction order: Demolition before Masonry before Finishing.
  Specializes in building the step-by-step renovation plan with itemized costs.
  Does NOT perform building-physics due diligence; use expert-interviewer for that.
trigger_conditions:
  - Buyer asks for a renovation plan, sequence, or step-by-step breakdown
  - Buyer asks about construction order: "mit kell előbb csinálni"
  - Buyer asks for a complete renovation schedule including all phases
  - Buyer asks about technical feasibility of specific work items
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

### Mandatory Sequencing Rules

The Construction Planner MUST enforce the following order. Any deviation
MUST produce a warning.

| Step | Phase | Work Items | Dependency |
|------|-------|------------|------------|
| 0 | Site Prep | Salak bontás és elszállítás (if pre-1960 slab) | None |
| 1 | Bontás (Demolition) | Wall removal, floor tile removal, fixture removal | Step 0 complete |
| 2 | Kőműves (Masonry) | New walls, floor reinforcement, door openings | Step 1 complete |
| 3 | Gépészet (Rough-in) | Electrical wiring, plumbing pipes, HVAC ducts | Step 2 complete |
| 4 | Vakolás & Glettelés | Plastering, sanding, wallpaper scraping if needed | Step 3 complete |
| 5 | Burkolás (Flooring) | Tile, laminate, parquet, bathroom waterproofing | Step 4 dry |
| 6 | Festés & Szerelés | Painting, fixtures, switches, sockets, doors | Step 5 complete |

### Cost Itemization Rules

Each step in the plan MUST include itemized costs:

| Phase | Material Cost Range | Labor Cost Range | Notes |
|-------|-------------------|------------------|-------|
| Salak removal | 0 Ft (no material) | 3 000 - 5 000 Ft/nm | Pre-1960 only |
| Bontás (Demolition) | 0 - 50 000 Ft | 1 000 - 2 000 Ft/nm | Depends on scope |
| Kőműves (Masonry) | 249 000 - 402 000 Ft | 280 000 - 420 000 Ft | Depends on floor type |
| Gépészet (Rough-in) | 200 000 - 500 000 Ft | 500 000 - 1 000 000 Ft | Electrical + plumbing |
| Vakolás (Plaster) | 80 000 - 180 000 Ft | 180 000 - 380 000 Ft | Add scraping surcharge if wallpaper |
| Burkolás (Flooring) | 130 000 - 250 000 Ft | 300 000 - 600 000 Ft | Depends on tile size |
| Festés (Painting) | 50 000 - 100 000 Ft | 200 000 - 400 000 Ft | Final fixtures included |

### Edge Cases

| Condition | Handling |
|-----------|----------|
| Buyer wants only one phase (e.g. only flooring) | Skip earlier phases, but warn: "A burkolás előtt ellenőrizze, hogy az aljzat megfelelő-e" |
| Building has kohósalak AND buyer wants new walls | Step 0 (salak removal) MUST precede Step 2 (masonry). Warn if skipped. |
| Budget is too low for full sequence | Recommend phased approach: "Javasolt ütemezés: 1. Bontás, 2. Gépészet, 3. ..." |
| Buyer does not want structural changes | Skip Step 2 (masonry), adjust costs accordingly |
| Sawdust wallpaper present | Insert "Tapéta kaparás" sub-step before Step 4 plastering. Add surcharge. |
| Built-in shower specified | Mandate cement-based waterproofing in Step 5. Add material + labor line items. |

### Quality Gates

1. Every plan MUST follow the mandatory sequencing order
2. Every phase MUST include itemized material + labor cost ranges
3. If structural changes present, MUST generate Vibe Diff before finalizing
4. Every phase MUST reference corpus-supported price ranges
5. If confidence < 0.7 for any phase cost, MUST flag for Vibe Diff review
6. Every plan MUST include a total estimated range (low-mid-high)

## Scripts (Load on Demand)

### Sequencing Method

```text
1. Collect renovation scope from user input (demolition, masonry, rough-in, finishing)
2. Determine if building is pre-1960 (check for kohósalak risk)
3. Determine if walls have fűrészporos tapéta (check wall condition)
4. Determine shower type (épített zuhany vs zuhanytálca)
5. Build step sequence based on Mandatory Sequencing Rules
6. For each step:
   a. Look up cost range from reference tables
   b. Apply area scaling (user_area / reference_area)
   c. Apply condition surcharges (wallpaper, slag, waterproofing)
7. If confidence < 0.7 for any step: trigger Vibe Diff
8. If structural changes: trigger Vibe Diff with full reasoning
9. Return complete plan with all steps, costs, and warnings
```
