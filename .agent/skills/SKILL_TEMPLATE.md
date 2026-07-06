---
name: "<skill-name>"
description: >
  <Single paragraph. First sentence MUST be a crisp boundary definition.
  Second sentence MUST list the primary use case.
  Third sentence MUST list the one exclusion that prevents false triggers.
  Max 4 sentences.>
trigger_conditions:
  - <Hungarian keyword or pattern that triggers this skill>
  - <Repeat for each distinct trigger signal>
not_trigger_when:
  - <Specific exclusion that avoids context rot>
  - <If this exclusion matches, another skill name is the correct target>
depends_on:
  - <.agent/skills/<skill-name>/references/pricing.md>
  - <Data pipeline prerequisite (e.g., "run_ingestion.py must have been executed")>
calls:
  static_data: .agent/skills/<skill-name>/references/pricing.md
  mcp_tool: <tool-name-if-applicable>
---

## Instructions (Load after Metadata)

### Routing Criteria

<!-- Natural language rules the agent follows to decide YES/NO for this skill. -->

Example:
- If the user asks about a structural wall between floor beams, delegate to structural-core-specialist
- If the user asks about tile size pricing differences, delegate to finishing-interior-specialist
- If the user provides only district and room count without work scope, ask clarifying questions before proceeding

### Edge Cases

<!-- Known failure modes and how to handle them. -->

| Condition | Handling |
|-----------|----------|
| Corpus has < 3 similar quotes | Return estimate with explicit warning: "csak 30 idézetből álló adatbázis alapján" |
| Building era is unknown | Default to 1960-1990 era, flag as assumption in output |
| Ceiling height not provided | Assume 2.7m standard, flag as assumption |
| Price falls outside 2 standard deviations from mean | Flag as statistical outlier, request manual review |

### Quality Gates

<!-- What must be present in the output before returning to the caller. -->

1. Every HUF figure MUST include an `inflation_adjusted_to` date
2. Every response citing a source MUST use `[FORRÁS n]` notation
3. Every estimate MUST include low/mid/high range (not a single point)
4. If corpus < 3 similar cases, MUST append data limitation warning

## Scripts (Load on Demand — only when this skill is activated)

### Reference Tables

<!-- Link to the references/ subdirectory. Loaded only when estimation logic is invoked. -->

```markdown
See: `.agent/skills/<skill-name>/references/pricing.md`
```

### Calculation Methods

<!-- Step-by-step procedure for any computation this skill owns. -->

```text
1. Load pricing table from references/pricing.md
2. Match user-provided parameters (area, scope, quality tier) to the closest row
3. Apply area scaling factor = user_area / reference_area
4. Apply inflation factor from KSH CPI series
5. Compute low = material_cost * area_scale + labor_low * area_scale
6. Compute mid = material_cost * area_scale + (labor_low + labor_high) / 2 * area_scale
7. Compute high = material_cost * area_scale + labor_high * area_scale
8. Return {estimate_low_huf, estimate_mid_huf, estimate_high_huf, inflation_adjusted_to}
```
