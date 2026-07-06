---
name: finishing-interior-specialist
description: >
  Handle all surface finishing and interior fit-out work for Hungarian
  apartment renovations: tiling (burkolás) with cost variations by tile
  size (60x30, 60x60, 60x120, 80x80, 90x90, 120x120 cm) and quality
  tier (alsó to luxus), painting and plastering (festés, glettelés)
  including sawdust wallpaper removal (fűrészporos tapéta), laminate
  flooring installation (laminált padló) with XPS underlayment,
  suspended ceilings (gipszkarton álmennyezet), waterproofing
  (cementbázisú vs diszperziós vízszigetelés) with application rules,
  interior doors (beltéri ajtók) by hinge type and door style, paint
  quantity estimation, and skirting board installation. This skill owns
  finish quality tiers (Q3 plastering, alsó-közép-prémium-luxus
  categories) and all per-square-meter unit cost calculations. It does
  NOT handle structural work, MEP, or demolition.
trigger_conditions:
  - User asks about tiling costs by tile size or quality category
  - User asks about wall surface preparation (tapéta eltávolítás,
    csiszolás, glettelés, festés)
  - User asks about flooring (laminált, vinyl, XPS, párazáró fólia)
  - User asks about waterproofing type selection (cement vs diszperziós)
  - User asks about interior doors by hinge type or configuration
  - User asks about suspended ceiling costs per sqm
  - User asks about paint quantity estimation (mennyi festék kell)
  - Hungarian keywords: "burkolás", "csempe", "járólap", "glettelés",
    "festés", "tapéta", "laminált", "vízszigetelés", "ajtó",
    "gipszkarton álmennyezet", "szegőléc", "XPS", "fugázás"
not_trigger_when:
  - User asks about structural wall construction or floor reinforcement
  - User asks about demolition debris removal or tonnage
  - User asks about electrical, plumbing, or AC installation
  - User asks about kitchen cabinet construction (use fit-out specialist)
depends_on:
  - Data pipeline: interior finishing pricing references
  - .agent/skills/finishing-interior-specialist/references/pricing.md
calls:
  static_data: .agent/skills/finishing-interior-specialist/references/pricing.md
---
