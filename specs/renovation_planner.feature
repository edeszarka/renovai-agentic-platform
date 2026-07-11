Feature: Construction Planner — Technical Sequencing & Cost Logic (Tab 2)
  As a renovation planner
  I want the Construction Planner to enforce correct technical sequencing
  So that cost estimates reflect realistic construction order

  Background:
    Given the system has a Construction Planner agent specialized in technical sequencing
    And the system enforces: Demolition before Masonry before Finishing
    And every response includes a confidence score from ConfidenceModel
    And all estimates MUST be in HUF, adjusted for inflation using KSH CPI data
    And all phase names and descriptions MUST be in Hungarian by default

  Scenario: Full renovation strictly follows Demolition -> Masonry -> Finishing order
    Given a buyer requests a full renovation plan for a 55 nm apartment
    Including demolition, new wall construction, electrical, plumbing, and finishing
    When the Construction Planner generates the sequence
    Then Step 1 MUST be "Bontás" (Demolition) including slag removal if applicable
    And Step 2 MUST be "Kőműves" (Masonry) including new walls and floor reinforcement
    And Step 3 MUST be "Gépészet" (Plumbing/Electrical rough-in)
    And Step 4 MUST be "Vakolás és glettelés" (Plastering and sanding)
    And Step 5 MUST be "Burkolás" (Flooring and tiling)
    And Step 6 MUST be "Festés és szerelés" (Painting and fixture installation)
    And any deviation from this order MUST produce a warning

  Scenario: Slag removal must precede all other demolition work
    Given the building was constructed before 1960
    And the floor construction contains kohósalak (slag)
    When the Construction Planner generates the sequence
    Then "Salak bontás és elszállítás" (slag removal and disposal) MUST be Step 0
    And Step 0 MUST complete before any other demolition begins
    And the cost for slag removal MUST be itemized separately at 3 000 - 5 000 Ft/nm
    And the plan MUST warn: "Salak eltávolítás előtt statikus szakvélemény szükséges"

  Scenario: Sawdust wallpaper forces an explicit scraping step before plastering
    Given the walls are covered in fűrészporos tapéta (sawdust wallpaper)
    When the Construction Planner generates the finishing sequence
    Then "Tapéta kaparás" (wallpaper scraping) MUST appear as a distinct step
    And this step MUST precede "Csiszolás" (sanding) and "Vakolás" (plastering)
    And the estimate MUST include an extra 80 000 - 100 000 Ft for materials
    And the estimate MUST include an extra 180 000 - 280 000 Ft for labor
    And the plan MUST note: Q3 plastering quality is required after scraping

  Scenario: Waterproofing type depends on shower configuration
    Given the plan includes a bathroom renovation with "épített zuhany" (built-in shower)
    When the Construction Planner selects the waterproofing method
    Then the plan MUST specify cement-based waterproofing (1 komponensű)
    And the plan MUST specify 3 to 4 bags of waterproofing compound
    And the material cost MUST be between 130 000 Ft and 180 000 Ft

    When the shower type is "zuhanytálca" (shower tray) with 2 glass sides
    Or the fixture is a "fürdőkád" (bathtub)
    Then dispersion-based waterproofing IS sufficient
    And the material cost MUST be between 70 000 Ft and 100 000 Ft

  Scenario: Construction Planner must produce a Vibe Diff before finalizing plan
    Given the Construction Planner has generated a full renovation sequence
    When the plan includes any structural modification (wall removal, reinforcement)
    Then the Construction Planner MUST NOT emit the final plan directly
    And the system MUST generate a Vibe Diff explaining the reasoning
    And the Vibe Diff explanation_hu MUST be in Hungarian
    And the Vibe Diff MUST include: "Why each step is ordered as proposed"
    And the Vibe Diff MUST explain: "Cost impact of structural vs cosmetic choices"
    And the final plan MUST only be emitted after human review of the Vibe Diff

  Scenario: Full vs Partial renovation toggle changes phase count and costs
    Given a buyer selects "Teljes felújítás" (Full renovation) for a 55 nm apartment
    When the Construction Planner generates the sequence
    Then all 7 phases MUST be present (Step 0 through Step 6)
    And the total estimate MUST include all phases

    Given a buyer selects "Részleges felújítás" (Partial renovation) with only flooring and painting
    When the Construction Planner generates the sequence
    Then only the selected phases MUST be included
    And omitted phases MUST NOT appear in the cost total

  Scenario: Drywall installation appears after masonry but before rough-in
    Given a buyer includes drywall (gipszkarton) in their renovation scope
    When the Construction Planner generates the sequence
    Then "Gipszkarton és álmennyezet" MUST appear after masonry and before MEP rough-in
    And the material cost MUST use 5 000 - 6 200 Ft/m² range
    And the labor cost MUST use 9 500 - 11 500 Ft/m² range

  Scenario: Windows/doors replacement ordered after masonry, before rough-in
    Given a buyer includes window and door replacement (nyílászáró csere)
    When the Construction Planner generates the sequence
    Then "Nyílászáró csere" MUST appear after masonry and before MEP rough-in
    And the window count MUST be estimated as area_sqm / 15
    And the material cost MUST use 200 000 - 400 000 Ft per unit range
    And the plan MUST warn about window installation timing relative to MEP

  Scenario: Insulation phase appears after rough-in but before finishing
    Given a buyer includes insulation (szigetelés) in their scope
    When the Construction Planner generates the sequence
    Then "Szigetelés" MUST appear after MEP rough-in and before plastering
    And the material cost MUST use 2 500 - 5 000 Ft/m² range
    And the labor cost MUST use 2 000 - 4 000 Ft/m² range

  Scenario: Heating and AC appear as separate additions within MEP rough-in
    Given a buyer includes fűtésrendszer (heating system) in their scope
    When the Construction Planner generates the MEP phase
    Then the rough-in description MUST include "fűtéscsövek/radiátorok"
    And the material cost MUST be increased by 300 000 - 600 000 Ft
    And the labor cost MUST be increased by 300 000 - 500 000 Ft

    Given a buyer includes klíma (air conditioning) in their scope
    When the Construction Planner generates the MEP phase
    Then the rough-in description MUST include "klíma előkészítés"
    And the material cost MUST be increased by 250 000 - 450 000 Ft
    And the labor cost MUST be increased by 150 000 - 300 000 Ft

  Scenario: Kitchen installation adds to painting/fixtures phase
    Given a buyer includes kitchen (konyhabútor) in their scope
    When the Construction Planner generates the sequence
    Then the painting/fixtures phase description MUST include "konyhabútor szerelés"
    And the material cost MUST be increased by 300 000 Ft
    And the labor cost MUST be increased by 150 000 - 250 000 Ft

  Scenario: Sparse-category work types produce a data-limitation warning
    Given a buyer selects one or more of: szigetelés, nyílászáró, konyha, fürdő, gipszkarton, klíma, fűtésrendszer
    When the Construction Planner generates the sequence
    Then the response MUST include a warning containing "Adat korlátozás"
    And the warning MUST list each selected sparse category by name
    And the warning MUST state the pricing is "tájékoztató jellegű"
