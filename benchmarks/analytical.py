"""
Analytical Benchmark (TPC-H or TPC-DS).
Runs each query in the suite independently and records per-query latency.
Warmup runs are not verified or recorded.
Verification (if answer files exist) runs on the first timed run only.

Answer verification is only meaningful against the pristine loaded state. When the
table has been mutated by data-maintenance rounds (verify=False), the reference
answers no longer apply and are skipped.
"""
from __future__ import annotations

import functools
import operator
import statistics

from benchmarks.runner import BenchmarkRunner, QueryResult
from benchmarks.suite import Suite


def run(
    runner: BenchmarkRunner,
    suite: Suite,
    namespace: str,
    scale_factor: int,
    warmup_runs: int = 1,
    benchmark_runs: int = 3,
    verify: bool = True,
) -> list[QueryResult]:
    answer_dir = suite.answer_dir(scale_factor)
    query_files = sorted(suite.query_dir.glob("q*.sql"))
    results: list[QueryResult] = []

    for qfile in query_files:
        sql = qfile.read_text()
        query_name = qfile.stem  # e.g. "q01"
        answer_path = answer_dir / f"{query_name}.csv"

        for _ in range(warmup_runs):
            try:
                runner.engine.run_query(sql, namespace)
            except Exception:
                pass

        for run_idx in range(benchmark_runs):
            # Only verify on the first timed run — answer check doesn't need repeating
            check = verify and run_idx == 0
            result = runner.time_query(
                sql=sql,
                query_name=query_name,
                benchmark="analytical",
                namespace=namespace,
                run=run_idx,
                answer_path=answer_path if check else None,
            )
            results.append(result)

            status_parts = [f"{result.elapsed_seconds:.3f}s"]
            if result.result_correct is True:
                status_parts.append("✓")
            elif result.result_correct is False:
                status_parts.append("MISMATCH")
            elif check and not answer_path.exists():
                status_parts.append("(no answer file)")
            if result.error:
                status_parts = [f"ERROR: {result.error}"]

            print(f"  {query_name} run {run_idx}: {' '.join(status_parts)}")

    _print_score(results, suite, scale_factor)
    return results


def _print_score(results: list[QueryResult], suite: Suite, scale_factor: int) -> None:
    """
    Print a power-like score using the median time per query across runs.
    Uses the same geometric-mean formula as the TPC-H power score but without
    RF1/RF2 (not applicable to the analytical benchmark).
    """
    from collections import defaultdict
    by_query: dict[str, list[float]] = defaultdict(list)
    for r in results:
        if not r.error:
            by_query[r.query].append(r.elapsed_seconds)

    n = suite.query_count
    if len(by_query) != n:
        return

    medians = [statistics.median(times) for times in by_query.values()]
    product = functools.reduce(operator.mul, medians)
    score = round((3600 * scale_factor) / (product ** (1 / n)), 2)
    print(f"\n  analytical_score = {score:.2f} @{scale_factor} "
          f"(geo mean of {n} median query times, no RF)")
