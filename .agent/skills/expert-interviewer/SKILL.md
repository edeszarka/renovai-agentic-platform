---
name: expert-interviewer
description: >
  Expert Interviewer — building-physics due diligence for pre-purchase inspection.
  Specializes in identifying "Red Flags" such as kohósalak (slag) in pre-1960
  slabs, aluminium wiring in 1970s panels, foundation risks in pre-1920 brick
  buildings, and other structural deal-breakers. Does NOT produce cost estimates;
  use cost_estimate for pricing.
trigger_conditions:
  - Buyer asks about building condition, red flags, mire figyeljek, mit nézzek meg
  - Buyer mentions specific building era or construction type
  - Buyer asks about risks specific to panel, tégla, or pre-1920 buildings
  - Buyer asks about kohósalak (slag), aluminium wiring, foundation issues
not_trigger_when:
  - Buyer asks only for cost estimate or price range (use cost_estimate)
  - Buyer asks about market statistics or averages (use market_data_query)
  - Buyer uploads a quote file (use ingestion sandbox)
depends_on:
  - .agent/skills/expert-interviewer/references/red_flags.md
  - Source 5 corpus for building-era-specific risk patterns
calls:
  static_data: .agent/skills/expert-interviewer/references/red_flags.md
  mcp_tool: get_due_diligence_advice
---

## Instructions (Load after Metadata)

### Hungarian Language Mandate

ALL red-flag titles, descriptions, recommended actions, and user-facing output
MUST be in Hungarian. English translations are only for internal review.
The following Hungarian terms are MANDATORY:
- "Kohósalak" (not "slag")
- "Fűrészporos tapéta" (not "sawdust wallpaper")
- "Alumínium vezeték" (not "aluminium wiring")
- "Födém megerősítés" (not "floor reinforcement")
- "Betontálcás födém" (not "concrete tray slab")
- "Tégla boltíves acél gerendás födém" (not "brick arch steel beam slab")

### Red-Flag Identification (Priority Order)

The Expert Interviewer MUST check red flags in this priority order, because
structural risks (high cost, deal-breaker potential) must be surfaced before
cosmetic issues. All output MUST be in Hungarian.

1. **PRE-1960 SLAB: Kohósalak (Slag)**
   - Check: Was the building constructed before 1960? Is the floor "acél gerendás födém kohósalakkal"?
   - Risk: Slag removal costs 3 000 - 5 000 Ft/nm. Leaving slag in place = future floor sagging.
   - Action: Flag as CRITICAL. Recommend structural engineer inspection.

2. **1970s PANEL: Aluminium Wiring**
   - Check: Was the building constructed between 1965-1985? Is it panel construction?
   - Risk: Aluminium wiring = fire hazard. Full rewiring costs 3 000 - 5 000 Ft/nm.
   - Action: Flag as HIGH. Recommend licensed electrician inspection.

3. **PRE-1920 BRICK: Foundation Settlement**
   - Check: Was the building constructed before 1920? Is it "tégla" construction?
   - Risk: Foundation settlement = cracks, uneven floors. "Tégla boltíves acél gerendás födém" needs reinforcement.
   - Action: Flag as HIGH. Recommend structural engineer + potential floor reinforcement.

4. **1960-1990 PANEL: Strang (Stack) Ventilation**
   - Check: Is it panel construction with central ventilation stacks?
   - Risk: Original stack ventilation may be blocked or inefficient.
   - Action: Flag as MEDIUM. Recommend checking airflow before purchase.

5. **HUMIDITY/MOULD: Bathroom & Kitchen Waterproofing**
   - Check: Visible water stains, peeling paint, musty smell.
   - Risk: Hidden mould = health hazard + expensive remediation.
   - Action: Flag as MEDIUM. Recommend thermal camera inspection.

6. **WINDOWS: Original wooden frames**
   - Check: Are windows original single-pane wooden frames?
   - Risk: Replacement cost 200 000 - 400 000 Ft per window.
   - Action: Flag as LOW. Note in checklist.

### Edge Cases

| Condition | Handling |
|-----------|----------|
| Building era unknown | Default to 1960-1990 era. Flag as assumption. Confidence -= 0.1 |
| No floor construction info | Ask buyer to check: "Kérem nézze meg, milyen a födém szerkezete (panel/tégla/acél gerendás)" |
| Buyer reports no known issues | Still check building-era defaults — many issues are invisible to non-experts |
| Multiple red flags found | Order by risk level (CRITICAL > HIGH > MEDIUM > LOW) in the output |
| All checks pass (no red flags) | Return positive summary with confidence >= 0.85 |

### Quality Gates

1. Every red flag MUST include a risk level (CRITICAL/HIGH/MEDIUM/LOW)
2. Every red flag MUST include a recommended action
3. Every response MUST include overall_risk and summary_hu
4. If confidence < 0.7, MUST trigger Vibe Diff before emitting
5. Every response MUST include a confidence score from ConfidenceModel

## Scripts (Load on Demand)

### Reference Tables

See: `.agent/skills/expert-interviewer/references/red_flags.md`

### Red-Flag Assessment Method

```text
1. Determine building construction era from user input
2. If era unknown, default to 1960-1990 and flag as assumption
3. Run priority-ordered checks (see Red-Flag Identification above)
4. For each flag found:
   a. Document the specific risk
   b. Assign risk level based on cost impact and safety
   c. Generate recommended action
5. Sort results by risk level (CRITICAL first)
6. Compute confidence from number of confirmed checks vs defaults
7. If confidence < 0.7: trigger Vibe Diff + Green Team review
8. Return structured assessment
```
