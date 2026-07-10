# RenovAI 2.0 — Tests & Testing Guide

## Quick Start

```powershell
# 1. Install test dependencies
pip install -e ".[test]"

# 2. Run all tests
python -m pytest tests/ -v

# 3. Run only the new A2A handler tests
python -m pytest tests/ -v -k "interview or planning"

# 4. Load test (no external deps)
python scripts/load_test.py --concurrency 5 --requests 20
```

---

## What We Test

### 1. Gateway Intent Classification (Unit)

| Test | File | What it proves |
|------|------|----------------|
| Keyword match routes `expert_interview` | `tests/test_gateway.py` | "mire figyeljek" maps to EXPERT_INTERVIEW with confidence >= 0.7 |
| Keyword match routes `construction_planning` | `tests/test_gateway.py` | "sorrend" maps to CONSTRUCTION_PLANNING |
| Combined intent detection | `tests/test_gateway.py` | "mennyibe kerül + mire figyeljek" -> COMBINED |
| Low confidence triggers clarification | `tests/test_gateway.py` | Unknown query returns `clarification_needed=True` |
| LLM fallback when API key is missing | `tests/test_gateway.py` | Falls back to keyword-only routing |

### 2. Expert Interviewer Handler (Unit)

| Test | File | What it proves |
|------|------|----------------|
| Pre-1960 building with slag -> CRITICAL red flag | `tests/test_handlers.py` | `handle_expert_interview` returns kohósalak CRITICAL |
| 1970s panel with alumínium wiring -> HIGH | `tests/test_handlers.py` | Aluminium wiring flagged with estimated cost |
| Pre-1920 brick foundation -> HIGH | `tests/test_handlers.py` | Foundation settlement risk included |
| Sawdust wallpaper -> MEDIUM | `tests/test_handlers.py` | Scraping surcharge in red_flags |
| Clean modern building -> LOW risk, no flags | `tests/test_handlers.py` | Positive summary, 0 red flags |
| Confidence < 0.7 triggers Vibe Diff review | `tests/test_handlers.py` | Output includes confidence.score < 0.7 |

### 3. Construction Planner Handler (Unit)

| Test | File | What it proves |
|------|------|----------------|
| Full renovation has 6-7 phases in correct order | `tests/test_handlers.py` | Phases follow Demolition -> Masonry -> Rough-in -> Plaster -> Floor -> Paint |
| Pre-1960 with slag -> Phase 0 (salak removal) | `tests/test_handlers.py` | Step 0 exists, cost 3 000-5 000 Ft/nm |
| Sawdust wallpaper -> scraping surcharge + warning | `tests/test_handlers.py` | Warning present in output |
| Built-in shower -> cement waterproofing | `tests/test_handlers.py` | Warning about cement-based waterproofing |
| Total estimate is low/mid/high with nonzero values | `tests/test_handlers.py` | total_low < total_mid < total_high |

### 4. Policy Service + ABAC (Integration)

| Test | File | What it proves |
|------|------|----------------|
| `expert_interviewer` role can `assess_risk` | `tests/test_policy.py` | Structural gate passes |
| `construction_planner` role can `generate_sequence` | `tests/test_policy.py` | Structural gate passes |
| Wrong role blocked for `generate_advisory` | `tests/test_policy.py` | Structural gate denies |

### 5. Vibe Diff Engine (Integration)

| Test | File | What it proves |
|------|------|----------------|
| Generates VibeDiff from before/after snapshots | `tests/test_vibe_diff.py` | VibeDiff with non-empty explanation_hu |
| Fallback when LLM call fails | `tests/test_vibe_diff.py` | Returns graceful fallback message |
| VibeDiff stores separately from main report | `tests/test_vibe_diff.py` | vibe_id not in report, only referenced |

### 6. Gherkin Spec Validation (Scenario)

Not automated yet. Run manually by reading through:
- `specs/buyer_interview.feature` (5 scenarios for Expert Interviewer)
- `specs/renovation_planner.feature` (5 scenarios for Construction Planner)
- `specs/features/renovation_logic.feature` (3 scenarios for structural rules)
- `specs/cost_estimation.feature` (5 scenarios for cost pipeline)

---

## Manual Testing (No API Key Required)

### Test Gateway Keyword Routing

```powershell
python -c "
import asyncio
from orchestrator.gateway import Gateway, Intent

async def test():
    g = Gateway()

    tests = [
        ('mire figyeljek egy 1950-es panel lakásnál', Intent.EXPERT_INTERVIEW),
        ('milyen sorrendben kell felújítani egy lakást', Intent.CONSTRUCTION_PLANNING),
        ('mennyibe kerül a felújítás', Intent.COST_ESTIMATION),
        ('átlagos felújítási költség', Intent.MARKET_QUERY),
        ('feltöltök egy xlsx fájlt', Intent.INGESTION),
    ]

    for question, expected in tests:
        decision = await g.classify(question)
        passed = decision.primary_intent == expected or (
            expected == Intent.EXPERT_INTERVIEW and decision.primary_intent in (Intent.EXPERT_INTERVIEW, Intent.COMBINED)
        )
        status = 'PASS' if passed else 'FAIL'
        print(f'{status}: \"{question[:40]}...\" -> {decision.primary_intent} (conf={decision.confidence:.2f})')

asyncio.run(test())
"
```

### Test Expert Interviewer Handler (Standalone)

```powershell
python -c "
import asyncio
from orchestrator.handlers import handle_expert_interview

class MockPolicy:
    def check_structural(self, role, action, trace_id):
        from orchestrator.policy_service import PolicyCheckResult
        return PolicyCheckResult(passed=True, reason='mock', trace_id=trace_id, check_type='structural')
    async def check_semantic(self, args, trace_id):
        from orchestrator.policy_service import PolicyCheckResult
        return PolicyCheckResult(passed=True, reason='mock', trace_id=trace_id, check_type='semantic')

class MockRegistry:
    def get(self, name): return None
    def load_instructions(self, name): return ''

async def test():
    result = await handle_expert_interview(
        {'building_era': '1950', 'building_type': 'panel', 'area_sqm': 55, 'scope_flags': {'slag': True}},
        MockPolicy(), MockRegistry(), 'test-001',
    )
    print(f'Status: {result[\"status\"]}')
    print(f'Risk: {result[\"data\"][\"overall_risk\"]}')
    print(f'Red flags: {len(result[\"data\"][\"red_flags\"])}')
    for rf in result['data']['red_flags']:
        print(f'  [{rf[\"risk\"]}] {rf[\"title\"]}')

asyncio.run(test())
"
```

### Test Construction Planner Handler (Standalone)

```powershell
python -c "
import asyncio
from orchestrator.handlers import handle_construction_planning

class MockPolicy:
    def check_structural(self, role, action, trace_id):
        from orchestrator.policy_service import PolicyCheckResult
        return PolicyCheckResult(passed=True, reason='mock', trace_id=trace_id, check_type='structural')
    async def check_semantic(self, args, trace_id):
        from orchestrator.policy_service import PolicyCheckResult
        return PolicyCheckResult(passed=True, reason='mock', trace_id=trace_id, check_type='semantic')

class MockRegistry:
    def get(self, name): return None
    def load_instructions(self, name): return ''

async def test():
    result = await handle_construction_planning(
        {'area_sqm': 55, 'scope_flags': {'demolition': True, 'slag': True, 'built_in_shower': True}},
        MockPolicy(), MockRegistry(), 'test-002',
    )
    print(f'Status: {result[\"status\"]}')
    print(f'Phases: {len(result[\"data\"][\"phases\"])}')
    for p in result['data']['phases']:
        print(f'  Step {p[\"step\"]}: {p[\"name\"]}')
    e = result['data']['total_estimate']
    print(f'Total: {e[\"low_huf\"]:,} - {e[\"mid_huf\"]:,} - {e[\"high_huf\"]:,} HUF')
    print(f'Warnings: {len(result[\"data\"][\"warnings\"])}')

asyncio.run(test())
"
```

### Run the Load Test

```powershell
python scripts/load_test.py --concurrency 5 --requests 10 --verbose
```

Expected output shows p50/p95/p99 latencies for gateway, policy structural, policy semantic, and total stages.

### Generate SBOM

```powershell
python scripts/generate_sbom.py --generate --output data/sbom/spdx.json
python scripts/generate_sbom.py --verify
```

SBOM gate should PASS if no unexpected packages are installed.

---

## Running Red-Team Tests

```powershell
python -c "
import asyncio
from renovai.evals.red_team_tests import RedTeamRunner, RED_TEAM_CASES

class MockPolicy:
    def check_structural(self, role, action, trace_id):
        from orchestrator.policy_service import PolicyCheckResult
        return PolicyCheckResult(passed=True, reason='mock', trace_id=trace_id, check_type='structural')
    async def check_semantic(self, args, trace_id):
        from orchestrator.policy_service import PolicyCheckResult
        return PolicyCheckResult(passed=True, reason='mock', trace_id=trace_id, check_type='semantic')

class MockGateway:
    async def classify(self, question):
        from orchestrator.gateway import RoutingDecision
        return RoutingDecision(trace_id='rt-mock')

async def test():
    runner = RedTeamRunner(MockPolicy(), MockGateway())
    results = await runner.run_all()
    passed = sum(1 for r in results if r.passed)
    print(f'Red-team: {passed}/{len(results)} passed')

asyncio.run(test())
"
```

---

## Vibe Trajectory Audit (End-to-End)

```powershell
python -c "
import asyncio
from renovai.safety.vibe_trajectory import VibeTrajectoryTracker

async def test():
    t = VibeTrajectoryTracker()
    t.set_context('gw-e2e-test', 'cost_estimation')
    await t.record_step('intent_classification', 'gateway', 'classify', 'ok', 12.5, {'intent': 'cost_estimation'})
    await t.record_step('zero_ambient_authority', 'policy', 'check_structural', 'ok', 2.1, {'role': 'cost_estimator'})
    await t.record_step('vibe_diff', 'vibe_diff_engine', 'generate', 'ok', 150.0, {'vibe_id': 'vd-abc'})
    await t.record_step('green_team', 'green_team', 'evaluate', 'ok', 5.0, {'confidence': 0.85})
    r = t.generate_report()
    print(f'7-Pillar Safety Envelope: {\"PASSED\" if r[\"safety_envelope_passed\"] else \"FAILED\"}')

asyncio.run(test())
"
```
