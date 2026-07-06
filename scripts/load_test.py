#!/usr/bin/env python3
"""
End-to-End Load Testing Script for RenovAI 2.0.

Simulates parallel multi-agent sessions and measures latency across the
Gateway -> Sub-Agent -> Safety Harness trajectory.

Usage:
    python scripts/load_test.py --concurrency 10 --requests 50
    python scripts/load_test.py --concurrency 20 --requests 100 --verbose
"""

import os
import sys
import json
import time
import asyncio
import argparse
import statistics
from datetime import datetime
from typing import Any

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

TEST_QUERIES: list[dict[str, Any]] = [
    {"question_hu": "mennyibe kerül egy 55 nm-es panel lakás felújítása a 7. kerületben?", "expected_intent": "cost_estimation"},
    {"question_hu": "mire figyeljek egy 1980-as években épült tégla lakás vásárlásánál?", "expected_intent": "due_diligence"},
    {"question_hu": "átlagos felújítási költség nm ára Budapesten?", "expected_intent": "market_query"},
    {"question_hu": "feltöltök egy árajánlatot excelből", "expected_intent": "ingestion"},
    {"question_hu": "mennyi egy 70 nm-es lakás teljes felújítása villannyal, vízvezetékkel és burkolással?", "expected_intent": "cost_estimation"},
    {"question_hu": "piros zászlók panel lakásnál, salak probléma", "expected_intent": "due_diligence"},
    {"question_hu": "hány idézet van a rendszerben a 13. kerületre?", "expected_intent": "market_query"},
]


# ---------------------------------------------------------------------------
# Performance measurement
# ---------------------------------------------------------------------------

class PerfStats:
    """Collect latency statistics across a load test run."""

    def __init__(self):
        self._latencies: list[float] = []
        self._stage_latencies: dict[str, list[float]] = {
            "gateway": [],
            "policy_structural": [],
            "policy_semantic": [],
            "total": [],
        }
        self._errors: int = 0
        self._successes: int = 0

    def record(self, stage: str, latency_ms: float) -> None:
        self._stage_latencies.setdefault(stage, []).append(latency_ms)
        if stage == "total":
            self._latencies.append(latency_ms)

    def record_error(self) -> None:
        self._errors += 1

    def record_success(self) -> None:
        self._successes += 1

    def report(self) -> dict[str, Any]:
        def _perc(data: list[float], p: float) -> float:
            if not data:
                return 0.0
            return statistics.quantiles(data, n=100, method='hdi')[int(p) - 1] if len(data) >= 100 else sorted(data)[int(len(data) * p / 100)]

        report: dict[str, Any] = {
            "total_requests": self._successes + self._errors,
            "successes": self._successes,
            "errors": self._errors,
            "error_rate_pct": round(self._errors / max(1, self._successes + self._errors) * 100, 2),
        }
        for stage, latencies in self._stage_latencies.items():
            if not latencies:
                report[stage] = {"count": 0}
                continue
            report[stage] = {
                "count": len(latencies),
                "min_ms": round(min(latencies), 2),
                "p50_ms": round(statistics.median(latencies), 2),
                "p95_ms": round(_perc(latencies, 95), 2),
                "p99_ms": round(_perc(latencies, 99), 2),
                "max_ms": round(max(latencies), 2),
                "avg_ms": round(statistics.mean(latencies), 2),
            }
        return report


# ---------------------------------------------------------------------------
# Simulated agent session (no external LLM dependency)
# ---------------------------------------------------------------------------

class MockGateway:
    """Synchronous mock of the Gateway agent for load testing."""

    async def classify(self, question_hu: str) -> dict[str, Any]:
        """Simulate gateway classification with fake latency."""
        await asyncio.sleep(0.01)  # Simulate 10ms LLM call
        from orchestrator.gateway import _keyword_classify
        primary, secondary, confidence = _keyword_classify(question_hu)
        return {
            "primary_intent": primary,
            "secondary_intents": secondary,
            "confidence": confidence,
            "trace_id": f"lt-{datetime.now().timestamp():.0f}",
        }


class MockPolicyService:
    """Synchronous mock of the PolicyService for load testing."""

    async def check_structural(self, role: str, action: str, trace_id: str) -> dict[str, Any]:
        await asyncio.sleep(0.002)  # Simulate 2ms YAML lookup
        return {"passed": True, "reason": "Mock structural pass", "trace_id": trace_id}

    async def check_semantic(self, args: dict[str, Any], trace_id: str) -> dict[str, Any]:
        await asyncio.sleep(0.005)  # Simulate 5ms regex scan
        return {"passed": True, "reason": "Mock semantic pass", "trace_id": trace_id}


async def simulate_session(
    query: dict[str, Any],
    gateway: MockGateway,
    policy_service: MockPolicyService,
    stats: PerfStats,
) -> None:
    """Run a single end-to-end agent session and record latencies.

    Trajectory: Gateway.classify -> PolicyService.check_structural
    -> PolicyService.check_semantic -> handler dispatch.
    """
    start_total = time.monotonic()

    try:
        # Stage 1: Gateway intent classification
        t0 = time.monotonic()
        decision = await gateway.classify(query["question_hu"])
        stats.record("gateway", (time.monotonic() - t0) * 1000)

        # Stage 2: Structural policy check
        t0 = time.monotonic()
        sr = await policy_service.check_structural(
            decision["primary_intent"], "execute", decision["trace_id"],
        )
        stats.record("policy_structural", (time.monotonic() - t0) * 1000)

        if not sr["passed"]:
            stats.record_error()
            return

        # Stage 3: Semantic policy check
        t0 = time.monotonic()
        sem = await policy_service.check_semantic(
            {"question": query["question_hu"]}, decision["trace_id"],
        )
        stats.record("policy_semantic", (time.monotonic() - t0) * 1000)

        if not sem["passed"]:
            stats.record_error()
            return

        stats.record_success()

    except Exception:
        stats.record_error()
    finally:
        stats.record("total", (time.monotonic() - start_total) * 1000)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_load_test(concurrency: int, total_requests: int, verbose: bool = False) -> PerfStats:
    """Run the load test with the given concurrency and request count."""
    gateway = MockGateway()
    policy_service = MockPolicyService()
    stats = PerfStats()

    sem = asyncio.Semaphore(concurrency)
    completed = 0

    async def bounded_session(query: dict[str, Any]) -> None:
        nonlocal completed
        async with sem:
            await simulate_session(query, gateway, policy_service, stats)
            completed += 1
            if verbose and completed % 10 == 0:
                print(f"  Progress: {completed}/{total_requests} requests completed")

    # Build the task list (cycle through test queries)
    tasks = []
    for i in range(total_requests):
        query = TEST_QUERIES[i % len(TEST_QUERIES)]
        tasks.append(asyncio.create_task(bounded_session(query)))

    if verbose:
        print(f"Starting load test: concurrency={concurrency}, requests={total_requests}")

    await asyncio.gather(*tasks)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RenovAI 2.0 End-to-End Load Test",
    )
    parser.add_argument(
        "--concurrency", "-c", type=int, default=10,
        help="Number of concurrent simulated sessions",
    )
    parser.add_argument(
        "--requests", "-n", type=int, default=50,
        help="Total number of requests to simulate",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print progress every 10 requests",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    results = asyncio.run(
        run_load_test(args.concurrency, args.requests, args.verbose),
    )

    report = results.report()
    report["config"] = {
        "concurrency": args.concurrency,
        "requests": args.requests,
    }

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"\n{'='*60}")
        print(f"RenovAI 2.0 Load Test Results")
        print(f"{'='*60}")
        print(f"Concurrency: {args.concurrency}")
        print(f"Total Requests: {report['total_requests']}")
        print(f"Successes: {report['successes']}")
        print(f"Errors: {report['errors']} ({report['error_rate_pct']}%)")
        print(f"\n--- Per-Stage Latencies ---")
        for stage, s in report.items():
            if stage in ("total_requests", "successes", "errors", "error_rate_pct", "config"):
                continue
            print(f"\n{stage.upper()}:")
            print(f"  Count: {s.get('count', 0)}")
            print(f"  P50:   {s.get('p50_ms', 'N/A'):>8} ms")
            print(f"  P95:   {s.get('p95_ms', 'N/A'):>8} ms")
            print(f"  P99:   {s.get('p99_ms', 'N/A'):>8} ms")
            print(f"  Avg:   {s.get('avg_ms', 'N/A'):>8} ms")
        print(f"\n{'='*60}")
        print(f"Total wall-clock P50:  {report.get('total', {}).get('p50_ms', 'N/A')} ms")
        print(f"Total wall-clock P95:  {report.get('total', {}).get('p95_ms', 'N/A')} ms")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
