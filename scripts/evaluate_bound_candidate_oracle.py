#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


ROOT_COUNT_RE = re.compile(r"Athena root candidate count: (\d+)")
BOUND_COUNT_RE = re.compile(r"Athena bound candidate count: (\d+)")
BOUND_CANDIDATE_RE = re.compile(
    r"Athena bound candidate idx=(\d+) id=(\d+) skeleton=(\d+) total_cost=([0-9.eE+-]+) rows=([0-9.eE+-]+) bindings=(.*)"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate PG18/Athena bound candidates against default and runtime oracle."
    )
    parser.add_argument("--pg-bin-dir", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--user", default=os.environ.get("USER", "postgres"))
    parser.add_argument("--database", default="athena_oracle_eval")
    parser.add_argument("--recreate-db", action="store_true")
    parser.add_argument("--schema-json", required=True)
    parser.add_argument("--queries-dir", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--statement-timeout-ms", type=int, default=5000)
    parser.add_argument("--max-oracle-candidates", type=int, default=0)
    parser.add_argument(
        "--write-valid-queries-dir",
        default="",
        help="If set, copy SQL files whose default EXPLAIN ANALYZE succeeds into this directory.",
    )
    parser.add_argument(
        "--write-oracle-eligible-queries-dir",
        default="",
        help="If set, copy SQL files that also have bound candidates and a valid oracle runtime into this directory.",
    )
    return parser.parse_args()


def psql_argv(pg_bin_dir, host, port, user, database, *, tuples_only=False):
    argv = [
        str(Path(pg_bin_dir) / "psql"),
        "-X",
        "-q",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        user,
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    if tuples_only:
        argv.extend(["-A", "-t"])
    return argv


def run_command(argv, *, input_text=None, timeout=None, check=True):
    return subprocess.run(
        argv,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=check,
    )


def load_schema(schema_json):
    return json.loads(Path(schema_json).read_text())


def create_table_statements(schema):
    statements = []
    for table in schema["tables"]:
        columns = ", ".join(f'{col["name"]} {col["type"]}' for col in table["columns"])
        primary_key = ""
        if "primary key" in table:
            if "column" in table["primary key"]:
                primary_key = f', primary key ({table["primary key"]["column"]})'
            else:
                cols = ", ".join(table["primary key"]["columns"])
                primary_key = f", primary key ({cols})"
        statements.append(f'create table {table["name"]} ({columns}{primary_key});')
    for table in schema["tables"]:
        for idx, fk in enumerate(table.get("foreign keys", []), start=1):
            if "column" in fk:
                local_cols = fk["column"]
                foreign_cols = fk["foreign column"]
            else:
                local_cols = ", ".join(fk["columns"])
                foreign_cols = ", ".join(fk["foreign columns"])
            statements.append(
                f"alter table {table['name']} add constraint {table['name']}_fk_{idx} "
                f"foreign key ({local_cols}) references {fk['foreign table']} ({foreign_cols});"
            )
    statements.append("analyze;")
    return statements


def ensure_database(args):
    postgres_argv = psql_argv(args.pg_bin_dir, args.host, args.port, args.user, "postgres")
    if args.recreate_db:
        sql = (
            f"select pg_terminate_backend(pid) from pg_stat_activity "
            f"where datname = '{args.database}' and pid <> pg_backend_pid();\n"
            f"drop database if exists {args.database};\n"
            f"create database {args.database};\n"
        )
        run_command(postgres_argv, input_text=sql, check=True)
        return

    check_db = run_command(
        postgres_argv + ["-Atqc", f"select 1 from pg_database where datname = '{args.database}'"],
        check=True,
    )
    if check_db.stdout.strip() != "1":
        run_command(postgres_argv, input_text=f"create database {args.database};\n", check=True)


def initialize_schema(args):
    schema = load_schema(args.schema_json)
    sql = "\n".join(create_table_statements(schema)) + "\n"
    run_command(
        psql_argv(args.pg_bin_dir, args.host, args.port, args.user, args.database),
        input_text=sql,
        check=True,
    )


def query_files(queries_dir, limit):
    files = sorted(Path(queries_dir).glob("*.sql"))
    if limit > 0:
        files = files[:limit]
    return files


def normalize_query_text(sql_text):
    sql_text = sql_text.strip()
    if sql_text.endswith(";"):
        sql_text = sql_text[:-1].rstrip()
    return sql_text


def run_explain_json(args, sql_text, *, enable_join_order_plans, analyze, force_idx=None, debug=False):
    timeout_seconds = max(10, args.statement_timeout_ms / 1000.0 + 5.0)
    settings = [
        f"set statement_timeout = {args.statement_timeout_ms};",
        "set client_min_messages = debug1;" if debug else "set client_min_messages = notice;",
        f"set enable_join_order_plans = {'on' if enable_join_order_plans else 'off'};",
        f"set athena_force_bound_candidate_idx = {force_idx if force_idx is not None else -1};",
    ]
    options = ["FORMAT JSON"]
    if analyze:
        options.extend(["ANALYZE", "TIMING OFF", "SUMMARY ON"])
    sql = "\n".join(settings + [f"explain ({', '.join(options)}) {normalize_query_text(sql_text)};"]) + "\n"
    proc = run_command(
        psql_argv(args.pg_bin_dir, args.host, args.port, args.user, args.database, tuples_only=True),
        input_text=sql,
        timeout=timeout_seconds,
        check=False,
    )
    return proc


def parse_explain_json_output(stdout_text):
    stdout_text = stdout_text.strip()
    if not stdout_text:
        return None
    data = json.loads(stdout_text)
    if isinstance(data, list) and data:
        return data[0]
    return data


def collect_bound_candidates(args, sql_text):
    proc = run_explain_json(
        args,
        sql_text,
        enable_join_order_plans=True,
        analyze=False,
        force_idx=None,
        debug=True,
    )
    candidates = []
    root_count = None
    bound_count = None

    for line in proc.stderr.splitlines():
        match = ROOT_COUNT_RE.search(line)
        if match:
            root_count = int(match.group(1))
        match = BOUND_COUNT_RE.search(line)
        if match:
            bound_count = int(match.group(1))
        match = BOUND_CANDIDATE_RE.search(line)
        if match:
            candidates.append(
                {
                    "idx": int(match.group(1)),
                    "bound_candidate_id": int(match.group(2)),
                    "skeleton_candidate_id": int(match.group(3)),
                    "total_cost": float(match.group(4)),
                    "rows": float(match.group(5)),
                    "bindings": match.group(6).strip(),
                }
            )

    return {
        "returncode": proc.returncode,
        "stderr": proc.stderr,
        "stdout": proc.stdout,
        "root_candidate_count": root_count,
        "bound_candidate_count": bound_count,
        "candidates": sorted(candidates, key=lambda row: row["idx"]),
    }


def evaluate_runtime(args, sql_text, *, enable_join_order_plans, force_idx=None):
    proc = run_explain_json(
        args,
        sql_text,
        enable_join_order_plans=enable_join_order_plans,
        analyze=True,
        force_idx=force_idx,
        debug=False,
    )
    result = {
        "returncode": proc.returncode,
        "stderr": proc.stderr.strip().splitlines()[:10],
        "planning_time_ms": None,
        "execution_time_ms": None,
    }

    if proc.returncode != 0:
        return result

    explain_obj = parse_explain_json_output(proc.stdout)
    if explain_obj is None:
        result["stderr"].append("missing EXPLAIN JSON output")
        result["returncode"] = 1
        return result

    result["planning_time_ms"] = explain_obj.get("Planning Time")
    result["execution_time_ms"] = explain_obj.get("Execution Time")
    return result


def summarize_oracle(default_runtime, planner_min_runtime, oracle_runtime):
    summary = {}
    if default_runtime.get("execution_time_ms") is not None and oracle_runtime.get("execution_time_ms") is not None:
        summary["default_minus_oracle_ms"] = (
            default_runtime["execution_time_ms"] - oracle_runtime["execution_time_ms"]
        )
    if planner_min_runtime.get("execution_time_ms") is not None and oracle_runtime.get("execution_time_ms") is not None:
        summary["planner_min_minus_oracle_ms"] = (
            planner_min_runtime["execution_time_ms"] - oracle_runtime["execution_time_ms"]
        )
    return summary


def write_query_copy(output_dir, sql_file, sql_text):
    outdir = Path(output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / sql_file.name).write_text(sql_text)


def main():
    args = parse_args()
    files = query_files(args.queries_dir, args.limit)
    if not files:
        raise SystemExit(f"No .sql files found under {args.queries_dir}")

    ensure_database(args)
    initialize_schema(args)

    report = {
        "database": args.database,
        "queries_dir": str(Path(args.queries_dir).resolve()),
        "schema_json": str(Path(args.schema_json).resolve()),
        "statement_timeout_ms": args.statement_timeout_ms,
        "max_oracle_candidates": args.max_oracle_candidates,
        "queries": [],
    }

    default_better = 0
    oracle_better = 0
    planner_min_matches_oracle = 0
    default_runtime_success_count = 0
    bound_collection_success_count = 0
    oracle_eligible_count = 0

    for sql_file in files:
        sql_text = sql_file.read_text(errors="ignore")
        row = {
            "path": str(sql_file.resolve()),
            "default_runtime": evaluate_runtime(
                args, sql_text, enable_join_order_plans=False, force_idx=None
            ),
        }

        bound_info = collect_bound_candidates(args, sql_text)
        row["root_candidate_count"] = bound_info["root_candidate_count"]
        row["bound_candidate_count"] = bound_info["bound_candidate_count"]
        row["bound_candidates"] = bound_info["candidates"]
        row["default_runtime_success"] = row["default_runtime"].get("execution_time_ms") is not None
        row["bound_collection_success"] = bound_info["returncode"] == 0 and bool(bound_info["candidates"])
        row["oracle_eligible"] = False

        if row["default_runtime_success"]:
            default_runtime_success_count += 1
            if args.write_valid_queries_dir:
                write_query_copy(args.write_valid_queries_dir, sql_file, sql_text)

        if bound_info["returncode"] != 0 or not bound_info["candidates"]:
            row["planner_min_runtime"] = {"returncode": 1, "stderr": ["failed to collect bound candidates"]}
            row["oracle_runtime"] = {"returncode": 1, "stderr": ["failed to collect bound candidates"]}
            report["queries"].append(row)
            continue

        bound_collection_success_count += 1

        planner_min = min(bound_info["candidates"], key=lambda candidate: candidate["total_cost"])
        row["planner_min_candidate_idx"] = planner_min["idx"]
        row["planner_min_runtime"] = evaluate_runtime(
            args, sql_text, enable_join_order_plans=True, force_idx=planner_min["idx"]
        )

        oracle_candidates = bound_info["candidates"]
        if args.max_oracle_candidates > 0:
            oracle_candidates = oracle_candidates[:args.max_oracle_candidates]

        runtime_results = []
        for candidate in oracle_candidates:
            runtime = evaluate_runtime(
                args, sql_text, enable_join_order_plans=True, force_idx=candidate["idx"]
            )
            runtime["idx"] = candidate["idx"]
            runtime["planner_total_cost"] = candidate["total_cost"]
            runtime_results.append(runtime)

        row["oracle_candidates_evaluated"] = runtime_results
        valid_runtime_results = [
            runtime for runtime in runtime_results if runtime.get("execution_time_ms") is not None
        ]

        if valid_runtime_results:
            oracle_best = min(valid_runtime_results, key=lambda runtime: runtime["execution_time_ms"])
            row["oracle_candidate_idx"] = oracle_best["idx"]
            row["oracle_runtime"] = oracle_best
            row["oracle_eligible"] = True
            oracle_eligible_count += 1
            if args.write_oracle_eligible_queries_dir:
                write_query_copy(args.write_oracle_eligible_queries_dir, sql_file, sql_text)
        else:
            row["oracle_runtime"] = {"returncode": 1, "stderr": ["no valid oracle runtime candidates"]}

        row["summary"] = summarize_oracle(
            row["default_runtime"], row["planner_min_runtime"], row["oracle_runtime"]
        )

        if row["default_runtime"].get("execution_time_ms") is not None and row["oracle_runtime"].get("execution_time_ms") is not None:
            if row["oracle_runtime"]["execution_time_ms"] < row["default_runtime"]["execution_time_ms"]:
                oracle_better += 1
            else:
                default_better += 1

        if (
            row["planner_min_runtime"].get("execution_time_ms") is not None
            and row["oracle_runtime"].get("execution_time_ms") is not None
            and row["planner_min_runtime"]["execution_time_ms"] == row["oracle_runtime"]["execution_time_ms"]
        ):
            planner_min_matches_oracle += 1

        report["queries"].append(row)

    report["summary"] = {
        "query_count": len(report["queries"]),
        "default_runtime_success_count": default_runtime_success_count,
        "bound_collection_success_count": bound_collection_success_count,
        "oracle_eligible_count": oracle_eligible_count,
        "oracle_better_than_default_count": oracle_better,
        "default_not_worse_than_oracle_count": default_better,
        "planner_min_equal_oracle_count": planner_min_matches_oracle,
    }

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))

    print(json.dumps(report["summary"], indent=2))
    print(output_path)


if __name__ == "__main__":
    main()
