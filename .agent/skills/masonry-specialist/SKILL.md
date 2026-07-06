---
name: masonry-specialist
description: >
  Handle structural masonry and concrete work inside Hungarian apartments:
  new wall construction between historical floor beams with or without floor
  reinforcement, subfloor leveling via slag removal or self-leveling compound,
  screed/concrete flooring (esztrich aljzatbeton), demolition debris weight
  estimation and removal logistics, window spaletta (régi tok) removal and
  frame restoration, and cement-based waterproofing for built-in showers.
  This skill owns all load-bearing calculations. It does NOT handle tiling,
  painting, doors, or MEP work.
trigger_conditions:
  - User asks about building a new wall where floor type matters (gerendák
    közé eső fal, födém megerősítés)
  - User asks about subfloor leveling involving slag, EPS, or concrete
  - User provides building era (1920 előtt, 1920-1965) with floor details
  - User asks about demolition debris weight, bag count, or removal costs
  - User asks about window spaletta restoration
  - Hungarian keywords: "födém megerősítés", "gerendák", "aljzatbeton",
    "esztrich", "sitt", "spaletta", "bontás", "salak", "betonozás"
not_trigger_when:
  - User only asks about surface finishing (delegate to finishing-specialist)
  - User only asks about interior doors, kitchen, appliances (delegate to fit-out agent)
  - User asks about cost without any structural details
depends_on:
  - .agent/skills/masonry-specialist/references/pricing.md
calls:
  static_data: .agent/skills/masonry-specialist/references/pricing.md
---

## Workflow

### Step 1: Classify the structural scenario
Determine the building era and floor construction type from the user's input.
- **Pre-1920**: téglas boltíves acél gerendás födém → requires floor reinforcement
- **1920-1965**: betontálcás födém vasbeton gerendákkal → wall can start from concrete tray
- **Unknown era**: ask for building year before proceeding

### Step 2: Select the pricing reference
Load `references/pricing.md` and select the relevant table based on the classified scenario. Do not load the full file if only one table is needed.

### Step 3: Compute the estimate
1. Match user parameters (wall length, area, ceiling height) to the closest reference row
2. Apply area scaling: `user_area / reference_area`
3. Apply inflation factor from the CPI calculator
4. Return low/mid/high range

### Step 4: Append structural warnings
- If pre-1920: MUST include "Födém megerősítés szükséges a gerendák között"
- If debris > 2 tonnes: include carrying-down cost estimate
- If fewer than 3 windows: flag that per-unit spaletta cost is higher

## Edge Cases

| Condition | Handling |
|-----------|----------|
| Building era unknown | Default to 1960-1990 era, flag as assumption |
| Ceiling height not provided | Assume 2.7m standard, flag as assumption |
| Debris estimate exceeds 5 tonnes | Flag as structural removal, recommend structural engineer review |
| Floor reinforcement required but user says no | Refuse estimate, explain safety risk |
