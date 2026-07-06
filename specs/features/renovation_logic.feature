Feature: Renovation Logic — Structural & Finishing Rules
  As a renovation cost estimator
  I want to apply building-era-specific rules and condition-dependent surcharges
  So that cost estimates reflect real-world construction complexity

  Background:
    Given the renovation corpus contains 30 historical quotes
    And pricing data is organized by building era, material type, and quality tier
    And all prices are in HUF and inflation-adjusted to current date

  Scenario: Pre-1920 building requires floor reinforcement for new walls
    Given a 50-60 nm apartment with 10m of new 10cm YTONG wall to build
    And 2 door openings of 0.75x2.1m each
    When the building was constructed before 1920
    And the floor construction is "tégla boltíves acél gerendás födém"
    And the ceiling height is 3.5m
    Then the estimate MUST include floor reinforcement between beams
    And the material cost MUST be 402 000 Ft
    And the labor cost MUST be between 320 000 Ft and 420 000 Ft
    And the system MUST warn: "Födém megerősítés szükséges a gerendák között"

    When the building was constructed between 1920 and 1965
    And the floor construction is "betontálcás födém vasbeton gerendákkal"
    And the ceiling height is between 2.8m and 3.3m
    Then the estimate MUST NOT include floor reinforcement
    And the material cost MUST be 249 000 Ft
    And the labor cost MUST be between 280 000 Ft and 380 000 Ft
    And the system MUST note: "Beton tálcáról indítható a fal, megerősítés nem szükséges"

  Scenario: Sawdust wallpaper triggers surface preparation surcharge
    Given a 50-60 nm apartment with 200-300 nm of wall surface to plaster and paint
    When the walls are covered with "fűrészporos tapéta" (sawdust wallpaper)
    Then the estimate MUST include an extra material cost of 80 000 - 100 000 Ft
    And the estimate MUST include an extra labor cost of 180 000 - 280 000 Ft
    And the system MUST require "tapéta kaparás" (wallpaper scraping) before sanding
    And the plastering quality MUST be Q3
    And the system MUST note that sawdust wallpaper implies walls are in worse condition
    Because the previous renovation skipped plastering entirely

    When the walls have a 40-60 year old "meszes réteg" (lime layer) without wallpaper
    Then the estimate MUST NOT include a surface preparation surcharge
    And the system MUST only require sanding before plastering and painting

  Scenario: Built-in shower mandates cement-based waterproofing
    Given a 4 nm bathroom with 15 nm single-layer and 30 nm double-layer waterproofing area
    And 14 meters of corner reinforcement tape
    And waterproofing up to 2.2m height in water-exposed areas
    When the shower type is "épített zuhany" (built-in shower)
    Then the waterproofing MUST be cement-based (1 komponensű)
    And the material cost MUST be between 130 000 Ft and 180 000 Ft
    And the system MUST require 3 to 4 bags of cement-based waterproofing compound

    When the shower type is "zuhanytálca" (shower tray) with 2 tiled sides and 2 glass sides
    Or the fixture is a "fürdőkád" (bathtub)
    Then dispersion-based waterproofing IS sufficient
    And the material cost MUST be between 70 000 Ft and 100 000 Ft
    And the system MUST NOT mandate cement-based waterproofing
