Feature: Expert Interviewer — Building-Physics Due Diligence (Tab 1)
  As a potential apartment buyer
  I want the Expert Interviewer to inspect building-physics risks
  So that I can identify hidden problems before purchase

  Background:
    Given the system has an Expert Interviewer agent specialized in building physics
    And the corpus contains Source 5 renovation knowledge including red-flag rules
    And every response includes a confidence score from ConfidenceModel

  Scenario: Pre-1960 slab building triggers salak (kohósalak) red flag
    Given a buyer asks about a 50 nm apartment in a building constructed in the 1950s
    And the floor construction is "acél gerendás födém kohósalakkal"
    When the Expert Interviewer assesses building-physics risks
    Then the response MUST include a "kohósalak" (slag) red flag
    And the response MUST state that slag removal costs 3 000 - 5 000 Ft/nm
    And the response MUST warn that leaving slag in place risks future floor sagging
    And the confidence score MUST be >= 0.7 because the corpus has multiple slag cases
    And the Vibe Diff MUST explain: "Why slag in this 1950s slab is a deal-breaker"

  Scenario: 1970s panel building triggers electrical rewiring red flag
    Given a buyer asks about a 65 nm apartment in a 1972 panel building
    When the Expert Interviewer assesses building-physics risks
    Then the response MUST flag aluminium wiring as a known 1970s panel issue
    And the response MUST estimate electrical rewiring at 3 000 - 5 000 Ft/nm
    And the response MUST recommend a licensed electrician inspection before purchase
    And if confidence < 0.7, the Gateway MUST flag for Vibe Diff review

  Scenario: Pre-1920 brick building triggers foundation and wall concerns
    Given a buyer asks about a 90 nm apartment in an 1910 historic brick building
    When the Expert Interviewer assesses building-physics risks
    Then the response MUST flag foundation settlement risk for pre-1920 buildings
    And the response MUST check for "tégla boltíves acél gerendás födém" floor type
    And the response MUST recommend structural engineer inspection
    And the Vibe Diff MUST summarize: "Why 1910s brick buildings need foundation checks"
    And the Vibe Diff MUST be presented for human approval before the final plan is emitted

  Scenario: Combined interview and cost query triggers multi-agent flow
    Given a buyer asks: "55 m²-es lakást nézek a 8. kerületben, 1960-as tégla épület,
    mennyi a felújítás és mire figyeljek?"
    When the Gateway classifies the intent
    Then the Gateway MUST detect both "expert_interview" and "cost_estimation" markers
    And the Gateway MUST route to Expert Interviewer for building-physics risks
    And the Gateway MUST route to Cost Estimator for pricing
    And both sub-agents MUST return a confidence score with their results
    And the Gateway MUST merge both responses into a single Hungarian answer
    And each section MUST be labelled with its source sub-agent

  Scenario: Low-confidence interview response triggers human-in-the-loop Vibe Diff
    Given a buyer asks about a rare building type not well-represented in the corpus
    When the Expert Interviewer returns with confidence < 0.7
    Then the Gateway MUST NOT emit the final interview report directly
    And the Gateway MUST invoke VibeDiffEngine to generate a plain-English summary
    And the Vibe Diff MUST explain: "Why the agent is uncertain about this assessment"
    And the Vibe Diff MUST be presented for human approval via GreenTeamService
    And the final report MUST only be emitted after human approval timestamp is recorded
