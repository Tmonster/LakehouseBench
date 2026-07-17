"""
Compaction benchmark.

Times a single compaction (merge of small data files) against the current table state,
and captures catalog health metrics before and after so the result is interpretable —
"compaction took 0.4s" means little without "it collapsed 812 files into 24".

Typical use is after N rounds of data maintenance (--dm-rounds N), which fragment the
fact tables into many small files plus delete files; compaction then measures how long
the engine takes to consolidate them.

run_sweep() extends this over a range of depths (--dm-rounds-start/--dm-rounds-end): it
re-provisions a fresh degenerate table at each depth and measures compaction, returning
one result per depth so a single run_id captures the whole time-vs-rounds curve.

Compaction support depends on the (engine, catalog) pair: DuckDB compacts DuckLake
(in-process merge_adjacent_files), Spark compacts Iceberg (rewrite_data_files). DuckDB's
Iceberg extension has no compaction step, so DuckDB+Iceberg is rejected up front by
run_benchmark via engine.supports_compaction(catalog).
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
    dm_rounds: int | None = None   # DM depth this measurement was taken at (sweep mode)


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


def run_sweep(runner, catalog, namespace, data_dir, tables, rounds: list[int]) -> list[CompactionResult]:
    """
    Measure compaction across several DM-round depths in one call — the caller records all
    results under a single run_id, so plot_compaction.py draws one curve (time vs rounds).

    Each depth needs its own *un-compacted* state: compaction is destructive and we want
    the cumulative R-round fragmentation, so the table is re-provisioned from base Parquet
    and R untimed rounds are re-applied before every measurement. This is O(sum(rounds)) of
    data-maintenance work plus one base reload per depth — intentionally the slow, faithful
    path.

    The engine is detached around catalog.provision because a DuckLake metadata file can be
    attached by only one connection at a time. After each measurement the catalog is
    reclaimed (expire + cleanup) so the prior depth's files don't accumulate on disk.
    """
    from benchmarks import data_maintenance

    engine = runner.engine
    results: list[CompactionResult] = []
    for i, r in enumerate(rounds):
        print(f"\n=== compaction after {r} DM round(s)  [{i + 1}/{len(rounds)}] ===")
        engine.teardown()                                    # release the metadata file
        catalog.provision(namespace=namespace, data_dir=data_dir, tables=tables)
        engine.setup(tables)
        data_maintenance.provision_rounds(engine, namespace, data_dir, r)
        res = run(runner, namespace)
        res.dm_rounds = r
        results.append(res)
        try:
            engine.reclaim(namespace)                        # drop this depth's orphaned files
        except Exception as e:
            print(f"  (reclaim skipped: {e})")
    return results
