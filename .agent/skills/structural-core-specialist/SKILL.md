---
name: structural-core-specialist
description: >
  Handle structural and heavy civil engineering work inside Hungarian
  apartments: load-bearing wall construction between historic floor beams,
  floor slab reinforcement (födém megerősítés) for pre-1920 buildings,
  subfloor leveling via slag removal + EPS + concrete pour vs. grinding +
  self-leveling compound, demolition and debris removal (sitt elszállítás)
  with weight/volume estimation, screed/concrete flooring (esztrich
  aljzatbeton), and window spaletta (régi tok) removal and frame
  restoration. This skill owns all structural calculations involving
  floor load capacity, material density tonnage, and reinforcement
  requirements. It does NOT handle surface finishes, tiling, painting,
  doors, or MEP fit-out.
trigger_conditions:
  - User asks about building a new wall where the floor type matters
    (gerendák közé eső fal, födém megerősítés)
  - User asks about subfloor leveling involving slag removal, EPS,
    concrete, or leveling compound (aljzatbeton, esztrich)
  - User provides building era (1920 előtt, 1920-1965) with floor
    construction details (tégla boltíves, betontálcás, acél gerenda)
  - User asks about demolition debris weight, bag count, or removal
    costs (sitt zsák, tonna, elvitel)
  - User asks about window spaletta (régi tok) removal and frame
    restoration
  - Hungarian keywords: "födém megerősítés", "gerendák között",
    "aljzatbeton", "esztrich", "sitt", "spaletta", "bontás",
    "salak", "kohósalak", "betonozás", "áthidaló"
not_trigger_when:
  - User only asks about surface finishing (tiling, painting, wallpaper)
  - User only asks about interior doors, kitchen, or sanitary ware
  - User only asks about MEP work (electrical, plumbing, AC)
  - User asks about cost estimation without structural details
depends_on:
  - Data pipeline: renovation pricing references
  - .agent/skills/structural-core-specialist/references/pricing.md
calls:
  static_data: .agent/skills/structural-core-specialist/references/pricing.md
---
