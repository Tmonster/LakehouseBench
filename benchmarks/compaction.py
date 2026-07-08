"""
Compaction benchmark.

Times a single compaction (merge of small data files) against the current table state,
and captures catalog health metrics before and after so the result is interpretable —
"compaction took 0.4s" means little without "it collapsed 812 files into 24".

Typical use is after N rounds of data maintenance (--dm-rounds N), which fragment the
fact tables into many small files plus delete files; compaction then measures how long
the engine takes to consolidate them.

DuckLake only for now (in-process merge_adjacent_files); Spark/Iceberg compaction lands
in a later phase.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from benchmarks.runner import BenchmarkRunner


@dataclass
class CompactionResult:
    elapsed_seconds: float
    query_start_time: str
    query_end_time: str
    stats_before: dict[str, int]
    stats_after: dict[str, int]
    error: str | None = None


def _fmt(stats: dict[str, int]) -> str:
    mb = stats["file_size_bytes"] / 1e6
    return (f"{stats['file_count']} files ({mb:.1f} MB), "
            f"{stats['delete_file_count']} delete files")


def run(runner: BenchmarkRunner, namespace: str) -> CompactionResult:
    engine = runner.engine
    stats_before = engine.table_stats(namespace)
    print(f"  before: {_fmt(stats_before)}")

    start_iso = datetime.now(timezone.utc).isoformat()
    start = time.perf_counter()
    error = None
    try:
        engine.optimize(namespace)
    except Exception as e:
        error = str(e)
    elapsed = round(time.perf_counter() - start, 4)
    end_iso = datetime.now(timezone.utc).isoformat()

    stats_after = engine.table_stats(namespace)
    print(f"  after:  {_fmt(stats_after)}")
    if error:
        print(f"  compaction ERROR: {error}")
    else:
        df = stats_before["file_count"] - stats_after["file_count"]
        ddel = stats_before["delete_file_count"] - stats_after["delete_file_count"]
        print(f"  compaction: {elapsed:.3f}s  "
              f"(data files {stats_before['file_count']}→{stats_after['file_count']}, "
              f"delete files {stats_before['delete_file_count']}→{stats_after['delete_file_count']}; "
              f"{df} data + {ddel} delete files removed)")

    return CompactionResult(
        elapsed_seconds=elapsed,
        query_start_time=start_iso,
        query_end_time=end_iso,
        stats_before=stats_before,
        stats_after=stats_after,
        error=error,
    )
