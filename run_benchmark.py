"""
Usage:
    python run_benchmark.py --engine duckdb --benchmark load --sf 10
    python run_benchmark.py --engine duckdb --benchmark analytical --sf 10
    python run_benchmark.py --engine duckdb --benchmark power --sf 10
    python run_benchmark.py --engine duckdb --benchmark throughput --sf 10
    python run_benchmark.py --engine duckdb --benchmark composite --sf 10
    python run_benchmark.py --engine duckdb --benchmark analytical --keep-tables --namespace my_ns
    python run_benchmark.py --engine duckdb --benchmark analytical --skip-datagen --namespace my_ns
"""
from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path

import yaml

from catalogs import load_catalog
from engines import load_engine
from benchmarks import record
from benchmarks.runner import BenchmarkRunner

BENCHMARK_CHOICES = ["load", "analytical", "power", "throughput", "composite"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=["duckdb", "spark", "athena"])
    parser.add_argument("--benchmark", required=True, choices=BENCHMARK_CHOICES)
    parser.add_argument("--sf", type=int, default=None, help="Override scale factor from config")
    parser.add_argument("--catalog-config", default="config/s3tables_catalog.yml")
    parser.add_argument("--benchmark-config", default="config/benchmark.yml")
    parser.add_argument("--namespace", default=None, help="Namespace (default: auto-generated UUID)")
    parser.add_argument("--keep-tables", action="store_true", help="Skip teardown after run")
    parser.add_argument("--skip-datagen", action="store_true", default=False,
                        help="Skip data generation and use existing data in --namespace")
    parser.add_argument("--update-streams", type=int, default=None,
                        help="Number of refresh sets in the throughput test (default: max(1, round(0.1 * sf)))")
    args = parser.parse_args()

    # Every recorded run must carry a machine identity — fail fast before any
    # expensive provisioning/data-gen if it's missing.
    if not os.environ.get("BENCH_INSTANCE_TYPE"):
        parser.error(
            "BENCH_INSTANCE_TYPE environment variable is not set. "
            "Set it (e.g. `export BENCH_INSTANCE_TYPE=m5.4xlarge`) so the machine "
            "is recorded with the results."
        )

    if args.skip_datagen and not args.namespace:
        parser.error("--namespace is required when --skip-datagen is set")

    if args.benchmark == "load" and args.skip_datagen:
        parser.error("--skip-datagen is not applicable to the load benchmark")

    catalog_cfg = yaml.safe_load(Path(args.catalog_config).read_text())
    bench_cfg = yaml.safe_load(Path(args.benchmark_config).read_text())

    scale_factor = args.sf or bench_cfg["scale_factor"]
    namespace = args.namespace or f"bench_{uuid.uuid4().hex[:8]}"
    data_dir = Path("data") / f"sf={scale_factor}"
    result_dir = Path(bench_cfg["result_dir"])

    catalog = load_catalog(catalog_cfg)
    engine = load_engine(args.engine, catalog)

    if args.benchmark not in engine.SUPPORTED_BENCHMARKS:
        supported = ", ".join(sorted(engine.SUPPORTED_BENCHMARKS))
        parser.error(
            f"'{args.benchmark}' is not supported by the {args.engine} engine "
            f"(supported: {supported})"
        )

    runner = BenchmarkRunner(
        engine=engine,
        catalog=catalog,
        engine_name=args.engine,
        scale_factor=scale_factor,
        result_dir=result_dir,
    )

    # The load benchmark times provisioning itself — skip the pre-provision step.
    # All other benchmarks provision first (unless --skip-datagen).
    if args.benchmark != "load" and not args.skip_datagen:
        print(f"Provisioning namespace '{namespace}'...")
        catalog.provision(namespace=namespace, data_dir=data_dir)

    print(f"\nRunning {args.benchmark} benchmark with {args.engine}...")
    engine.setup()
    run_id = uuid.uuid4().hex
    instance = record.bench_instance_type()
    engine_version = engine.version()

    # Helpers that inject the per-run context (run_id/instance/version) into rows.
    def q_row(qr):
        return record.time_row_from_query(
            qr, run_id=run_id, bench_instance_type=instance, engine_version=engine_version,
        )

    def rf_row(rf, benchmark):
        return record.time_row_from_refresh(
            rf, run_id=run_id, bench_instance_type=instance, engine=args.engine,
            engine_version=engine_version, scale_factor=scale_factor,
            benchmark=benchmark, namespace=namespace,
        )

    # benchmark_start_time is scoped to the query phase — provisioning (done above,
    # or inside load.run for the load benchmark) is excluded for non-load benchmarks.
    benchmark_start = record.now_iso()
    benchmark_end = None
    time_rows: list[tuple] = []
    power_score = throughput_score = composite_score = None
    load_error = None
    try:
        if args.benchmark == "load":
            from benchmarks import load
            result = load.run(
                catalog=catalog,
                engine_name=args.engine,
                namespace=namespace,
                data_dir=data_dir,
                scale_factor=scale_factor,
            )
            load_error = result.error

        elif args.benchmark == "analytical":
            from benchmarks import analytical
            results = analytical.run(
                runner=runner,
                namespace=namespace,
                scale_factor=scale_factor,
                warmup_runs=bench_cfg["warmup_runs"],
                benchmark_runs=bench_cfg["benchmark_runs"],
            )
            time_rows = [q_row(r) for r in results]

        elif args.benchmark == "power":
            from benchmarks import power
            result = power.run(
                runner=runner,
                namespace=namespace,
                data_dir=data_dir,
            )
            time_rows = [q_row(r) for r in result.stream]
            time_rows += [rf_row(rf, "power") for rf in filter(None, [result.rf1, result.rf2])]
            power_score = result.power_score
            if result.monitor_log:
                print(f"\nMonitor log: {result.monitor_log}")

        elif args.benchmark == "throughput":
            from benchmarks import throughput
            result = throughput.run(
                runner=runner,
                namespace=namespace,
                data_dir=data_dir,
                update_streams=args.update_streams,
            )
            time_rows = [q_row(r) for stream in result.streams for r in stream]
            time_rows += [rf_row(rf, "throughput") for rf in result.refresh_results]
            throughput_score = result.throughput_score
            if result.monitor_log:
                print(f"\nMonitor log: {result.monitor_log}")

        elif args.benchmark == "composite":
            from benchmarks import composite
            result = composite.run(
                runner=runner,
                namespace=namespace,
                data_dir=data_dir,
                update_streams=args.update_streams,
            )
            time_rows = [q_row(r) for r in result.power.stream]
            time_rows += [rf_row(rf, "composite") for rf in filter(None, [result.power.rf1, result.power.rf2])]
            time_rows += [q_row(r) for stream in result.throughput.streams for r in stream]
            time_rows += [rf_row(rf, "composite") for rf in result.throughput.refresh_results]
            power_score = result.power.power_score
            throughput_score = result.throughput.throughput_score
            composite_score = result.qphh
            for log in (result.power.monitor_log, result.throughput.monitor_log):
                if log:
                    print(f"\nMonitor log: {log}")

        benchmark_end = record.now_iso()

    finally:
        if benchmark_end is None:
            benchmark_end = record.now_iso()
        engine.teardown()
        if not args.keep_tables and not args.skip_datagen:
            print(f"\nTearing down namespace '{namespace}'...")
            catalog.teardown(namespace=namespace)

        # The load benchmark has no per-query rows — its single timed unit is the
        # provisioning bracketed by benchmark_start/end.
        if args.benchmark == "load":
            time_rows = [record.time_row_for_load(
                run_id=run_id, bench_instance_type=instance, engine=args.engine,
                engine_version=engine_version, scale_factor=scale_factor, namespace=namespace,
                query_start_time=benchmark_start, query_end_time=benchmark_end, error=load_error,
            )]

        log = record.log_row(
            run_id=run_id,
            benchmark_start_time=benchmark_start,
            benchmark_end_time=benchmark_end,
            bench_instance_type=instance,
            benchmark=args.benchmark,
            namespace=namespace,
            scale_factor=scale_factor,
            engine=args.engine,
            engine_version=engine_version,
            power_score=power_score,
            throughput_score=throughput_score,
            composite_score=composite_score,
            **catalog.catalog_info(),
        )
        record.append_log(result_dir, log)
        if time_rows:
            record.append_times(result_dir, time_rows)
        print(f"\nRecorded run {run_id}: 1 log row, {len(time_rows)} time row(s) → "
              f"{result_dir / record.LOGS_FILENAME}, {result_dir / record.TIME_FILENAME}")


if __name__ == "__main__":
    main()
