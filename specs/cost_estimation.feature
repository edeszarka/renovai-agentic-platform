Feature: Cost Ingestion & Estimation
  As the Gateway agent
  I want to parse HUF prices, adjust for inflation, and produce cost estimates
  So that buyers receive accurate, current-price renovation quotes

  Background:
    Given the system has an Ingestion Agent for XLSX parsing
    And an Inflation Calculator using KSH quarterly CPI data
    And a Cost Estimator using scope-matched averaging

  Scenario: Ingestion Agent parses diverse Hungarian HUF formats
    Given a contractor quote containing the following line items:
      | description    | amount_raw            |
      | Bontás         | "1,690,000"           |
      | Villanyszer.   | "963118 Ft"           |
      | Vakolás        | "nettó 2835000 Ft"    |
      | Festés         | "3798118 Ft."         |
      | Tervezés       | "0"                   |
    When the Ingestion Agent's number_parser processes each raw value
    Then the parsed integer for "1,690,000" MUST be 1 690 000
    And the parsed integer for "963118 Ft" MUST be 963 118
    And the parsed integer for "nettó 2835000 Ft" MUST be 2 835 000
    And the parsed integer for "3798118 Ft." MUST be 3 798 118
    And zero values MUST return None, not 0

  Scenario: Inflation adjustment applies separate labor and material factors
    Given a quote with target_date = "2025-06-01"
    And quarterly KSH CPI data:
      | year | quarter | component | index_value |
      | 2024 | 1       | labor     | 1.0         |
      | 2025 | 1       | labor     | 1.1         |
      | 2024 | 1       | materials | 1.0         |
      | 2025 | 1       | materials | 1.2         |
    When the quote has a line item with:
      | name     | labor_cost | material_cost | original_date |
      | Bontás   | 500 000    | 0             | 2024-03-15    |
    And the original_date maps to 2024-Q1
    Then the adjusted labor MUST be 500 000 * linear_interpolate(2024-Q1 -> 2025-Q1, 1.0 -> 1.1)
    And the adjusted material MUST be 0
    And the inflation_delta_pct MUST reflect only the labor component increase

  Scenario: Gateway routes cost query to Ingestion, then to Estimator
    Given a Hungarian user query: "mennyibe kerül felújítani egy 55 nm-es lakást a 8. kerületben"
    When the Gateway Agent classifies the intent
    Then the Gateway MUST route to Ingestion Agent if an XLSX attachment is present
    Or the Gateway MUST route to Cost Estimator if only parameters exist
    And the Cost Estimator MUST return a response with:
      - estimate_low_huf
      - estimate_mid_huf
      - estimate_high_huf
      - inflation_adjusted_to date
      - list of similar_quotes
      - warnings if corpus is thin (< 3 similar)

  Scenario: Gateway detects combined intent and merges sub-agent results
    Given a Hungarian user query: "55 m²-es lakást nézek a 8. kerületben, villany és vízvezeték is kell, mennyit költsek felújításra és mire figyeljek vásárlás előtt?"
    When the Gateway Agent classifies the intent
    Then the Gateway MUST detect both cost_estimation and due_diligence markers
    And the Gateway MUST call Market Analyst for cost estimation
    And the Gateway MUST call Due Diligence for advisory report
    And the Gateway MUST merge both responses into a single Hungarian answer
    And each section MUST be labelled with its source agent

  Scenario: Ingestion sandbox rejects malformed XLSX with structured error
    Given a buyer uploads a malformed XLSX file containing a billion-laughs XML entity expansion
    When the Ingestion Agent spawns the sandboxed subprocess
    Then the sandbox MUST terminate within 120 seconds
    And the sandbox MUST return a JSON error object with:
      - error: string describing the failure
      - type: exception class name
    And the sandbox MUST NOT write any data to the production database
    And the parent process MUST NOT merge the result into DB automatically
