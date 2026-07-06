# Vibe Diff Engine — Requirement Specification

## Purpose

The Vibe Diff Engine intercepts raw technical output from sub-agents (Expert
Interviewer, Construction Planner, Cost Estimator, Due Diligence Advisor) and
generates a plain-English summary explaining the "Why" behind the agent's
conclusions. This enables human-in-the-loop transparency before financial or
structural advice is emitted to the buyer.

## When Vibe Diff is Required

| Condition | Action |
|-----------|--------|
| Sub-agent confidence < 0.7 | Mandatory Vibe Diff + Green Team approval |
| Sub-agent confidence >= 0.7 but < 0.85 | Optional Vibe Diff (system discretion) |
| Sub-agent confidence >= 0.85 | Vibe Diff generated but may skip HITL |
| Structural modification proposed (wall removal, floor reinforcement) | Mandatory Vibe Diff |
| Combined intent (interview + cost) | One Vibe Diff per sub-agent |
| New building type not well-represented in corpus | Mandatory Vibe Diff |

## Vibe Diff Template

### Schema

```python
@dataclass(frozen=True)
class VibeDiff:
    vibe_id: str                    # "vd-<uuid hex 12>"
    trace_id: str                   # Trace from Gateway
    source: str                     # "expert_interview" | "construction_planning"
    explanation_hu: str             # Hungarian plain-English reasoning
    explanation_en: str             # English fallback for internal review
    key_drivers: list[str]          # Bullet-point causal chain
    before_snapshot: dict           # Input params received by sub-agent
    after_snapshot: dict            # Output produced by sub-agent
    model: str = "gemini-2.0-flash-lite"
```

### Explanation Guidelines

1. **Causal chain**: Always explain the cause-and-effect. Not "slab needs
   reinforcement" but "the building is pre-1920 with brick-arch steel floor —
   this means the new YTONG wall cannot sit on the existing slab without
   reinforcement between the steel beams."

2. **Hungarian first**: The primary explanation is in Hungarian for the buyer.
   English is the fallback for internal reviewers who may not speak Hungarian.

3. **Key drivers**: Exactly 2-5 bullet points enumerating the factors that
   most influenced the decision. Examples:
   - "Building era 1950s -> kohósalak risk"
   - "Wall condition: fűrészporos tapéta -> scraping surcharge"
   - "Shower type: épített zuhany -> cement-based waterproofing mandated"

4. **No embellishment**: Temperature 0.15. Stick to the facts from the before
   and after snapshots. Do not add hypothetical scenarios.

5. **Context hygiene**: The Vibe Diff is stored as a separate object, NOT
   embedded in the main report. The report references it by `vibe_id`.

## Integration Points

| Component | Role |
|-----------|------|
| `VibeDiffEngine` | Generates Vibe Diff via low-temperature LLM call |
| `Gateway` | Detects low confidence (< 0.7) and triggers Vibe Diff |
| `GreenTeamService` | Receives Vibe Diff in ApprovalRequest for HITL |
| `AuditStore` | Records vibe_id in immutable audit entry |
| `RenovAITracer` | Logs vibe diff generation as agent.think span |

## Edge Cases

| Condition | Handling |
|-----------|----------|
| LLM call fails to generate Vibe Diff | Return fallback: "A rendszer nem tudott automatikus magyarázatot generálni. Kérjük, ellenőrizze a nyers adatokat." |
| Multiple sub-agents in one session | One Vibe Diff per sub-agent, all bound to same trace_id |
| Human rejects the Vibe Diff | Buyer receives: "A tanácsadás jelenleg szakértői felülvizsgálat alatt áll." + request to retry later |
| Confidence exactly 0.7 | Treated as >= 0.7 (not requiring mandatory Vibe Diff) |
