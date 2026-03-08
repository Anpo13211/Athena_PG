#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path


BEGIN_MARKER = "__ATHENA_QUERY_BEGIN__"
END_MARKER = "__ATHENA_QUERY_END__"

ALT_RE = re.compile(r"Athena subquery slot (\d+) alternative (\d+)")
ROOT_COUNT_RE = re.compile(r"Athena root candidate count: (\d+)")
BOUND_COUNT_RE = re.compile(r"Athena bound candidate count: (\d+)")
ERROR_RE = re.compile(r"^(ERROR|psql:.*ERROR):", re.IGNORECASE)

FLAG_PATTERNS = {
    "from_subq": re.compile(r"\bfrom\s*\(", re.IGNORECASE | re.DOTALL),
    "join_subq": re.compile(r"\bjoin\s*\(", re.IGNORECASE | re.DOTALL),
    "cte": re.compile(r"^\s*with\b", re.IGNORECASE | re.DOTALL),
    "recursive_cte": re.compile(r"^\s*with\s+recursive\b", re.IGNORECASE | re.DOTALL),
    "exists": re.compile(r"\bexists\b", re.IGNORECASE),
    "in_select": re.compile(r"\bin\s*\(\s*select\b", re.IGNORECASE | re.DOTALL),
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Measure Athena subquery alternative-count distributions over a SQL workload."
    )
    parser.add_argument("--pg-bin-dir", required=True, help="Directory containing psql/createdb binaries.")
    parser.add_argument("--host", required=True, help="PostgreSQL host or Unix-domain socket directory.")
    parser.add_argument("--port", required=True, type=int, help="PostgreSQL port.")
    parser.add_argument("--user", default=os.environ.get("USER", "postgres"))
    parser.add_argument("--database", default="athena_measurement")
    parser.add_argument("--recreate-db", action="store_true", help="Drop and recreate the target database.")
    parser.add_argument("--schema-json", required=True, help="SQLStorm-style schema JSON.")
    parser.add_argument("--queries-dir", required=True, help="Directory containing .sql workload files.")
    parser.add_argument("--output-json", required=True, help="Where to write the aggregated JSON report.")
    parser.add_argument("--limit", type=int, default=0, help="Optional query limit for quick checks.")
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=3000,
        help="Per-query statement timeout in milliseconds. Use 0 to disable.",
    )
    parser.add_argument(
        "--isolated-per-query",
        action="store_true",
        help="Run each query in a separate psql process with a client-side timeout.",
    )
    return parser.parse_args()


def run_command(argv, *, input_text=None, check=True, timeout=None):
    return subprocess.run(
        argv,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
        timeout=timeout,
    )


def psql_argv(pg_bin_dir, host, port, user, database):
    return [
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
    ]


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
    run_command(psql_argv(args.pg_bin_dir, args.host, args.port, args.user, args.database), input_text=sql, check=True)


def classify_query(sql_text):
    return {name: bool(pattern.search(sql_text)) for name, pattern in FLAG_PATTERNS.items()}


def query_files(queries_dir, limit):
    files = sorted(Path(queries_dir).glob("*.sql"))
    if limit > 0:
        files = files[:limit]
    return files


def build_measurement_script(files, statement_timeout_ms):
    handle = tempfile.NamedTemporaryFile("w", delete=False, suffix=".sql")
    with handle:
        handle.write("set client_min_messages = debug1;\n")
        handle.write("set enable_join_order_plans = on;\n")
        handle.write(f"set statement_timeout = {statement_timeout_ms};\n")
        handle.write("\\pset pager off\n")
        for idx, sql_file in enumerate(files):
            sql_text = sql_file.read_text(errors="ignore").strip()
            if not sql_text:
                continue
            if not sql_text.endswith(";"):
                sql_text += ";"
            handle.write(f"\\echo {BEGIN_MARKER} {idx} {sql_file}\n")
            handle.write("\\o /dev/null\n")
            handle.write("explain " + sql_text + "\n")
            handle.write("\\o\n")
            handle.write(f"\\echo {END_MARKER} {idx}\n")
    return Path(handle.name)


def summarize_query_lines(sql_file, lines, *, timed_out=False):
    slot_map = {}
    root_count = None
    bound_count = None
    error_lines = []
    for line in lines:
        alt_match = ALT_RE.search(line)
        if alt_match:
            slot_id = int(alt_match.group(1))
            alt_id = int(alt_match.group(2))
            slot_map.setdefault(slot_id, set()).add(alt_id)
        root_match = ROOT_COUNT_RE.search(line)
        if root_match:
            root_count = int(root_match.group(1))
        bound_match = BOUND_COUNT_RE.search(line)
        if bound_match:
            bound_count = int(bound_match.group(1))
        if ERROR_RE.search(line):
            error_lines.append(line.strip())

    if timed_out:
        error_lines.append("TIMEOUT: client-side timeout expired")

    sql_text = sql_file.read_text(errors="ignore")
    flags = classify_query(sql_text)
    slot_counts = {str(slot_id): len(alts) for slot_id, alts in sorted(slot_map.items())}
    total_alts = sum(slot_counts.values())
    return {
        "path": str(sql_file),
        "flags": flags,
        "slot_count": len(slot_counts),
        "alternatives_per_slot": slot_counts,
        "total_alternatives": total_alts,
        "max_alternatives_per_slot": max(slot_counts.values(), default=0),
        "root_candidate_count": root_count,
        "bound_candidate_count": bound_count,
        "error": bool(error_lines),
        "error_lines": error_lines[:5],
    }


def parse_measurement_output(output_text, files):
    per_query = {}
    current_idx = None
    current_lines = []

    def finalize(idx, lines):
        if idx is None:
            return
        per_query[str(idx)] = summarize_query_lines(files[idx], lines)

    for raw_line in output_text.splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith(BEGIN_MARKER):
            parts = line.split(maxsplit=2)
            if len(parts) >= 2:
                finalize(current_idx, current_lines)
                current_idx = int(parts[1])
                current_lines = []
            continue
        if line.startswith(END_MARKER):
            finalize(current_idx, current_lines)
            current_idx = None
            current_lines = []
            continue
        if current_idx is not None:
            current_lines.append(line)

    finalize(current_idx, current_lines)
    return per_query


def run_isolated_measurement(args, files):
    per_query = {}
    timeout_seconds = None if args.statement_timeout_ms <= 0 else args.statement_timeout_ms / 1000.0
    statement_timeout_sql = "0" if args.statement_timeout_ms <= 0 else str(args.statement_timeout_ms)
    base_argv = psql_argv(args.pg_bin_dir, args.host, args.port, args.user, args.database)

    for idx, sql_file in enumerate(files):
        sql_text = sql_file.read_text(errors="ignore").strip()
        if not sql_text:
            per_query[str(idx)] = summarize_query_lines(sql_file, ["ERROR: empty query text"])
            continue
        if not sql_text.endswith(";"):
            sql_text += ";"
        input_sql = (
            "set client_min_messages = debug1;\n"
            "set enable_join_order_plans = on;\n"
            f"set statement_timeout = {statement_timeout_sql};\n"
            "\\o /dev/null\n"
            f"explain {sql_text}\n"
            "\\o\n"
        )
        try:
            proc = run_command(base_argv, input_text=input_sql, check=False, timeout=timeout_seconds)
            lines = proc.stdout.splitlines()
            per_query[str(idx)] = summarize_query_lines(sql_file, lines)
        except subprocess.TimeoutExpired as exc:
            combined = exc.stdout or ""
            lines = combined.splitlines() if isinstance(combined, str) else []
            per_query[str(idx)] = summarize_query_lines(sql_file, lines, timed_out=True)

    return per_query


def histogram(values):
    counter = Counter(values)
    return {str(key): counter[key] for key in sorted(counter)}


def aggregate_report(per_query):
    rows = list(per_query.values())
    total_alt_hist = histogram(row["total_alternatives"] for row in rows)
    slot_count_hist = histogram(row["slot_count"] for row in rows)

    per_slot_alt_values = []
    syntax_buckets = Counter()
    syntax_with_alts = Counter()
    for row in rows:
        for value in row["alternatives_per_slot"].values():
            per_slot_alt_values.append(value)
        for flag_name, enabled in row["flags"].items():
            if enabled:
                syntax_buckets[flag_name] += 1
                if row["total_alternatives"] > 0:
                    syntax_with_alts[flag_name] += 1

    return {
        "query_count": len(rows),
        "error_count": sum(1 for row in rows if row["error"]),
        "queries_with_subquery_alternatives": sum(1 for row in rows if row["total_alternatives"] > 0),
        "total_alternatives_per_query_histogram": total_alt_hist,
        "slot_count_per_query_histogram": slot_count_hist,
        "alternatives_per_slot_histogram": histogram(per_slot_alt_values),
        "syntax_bucket_counts": dict(sorted(syntax_buckets.items())),
        "syntax_bucket_with_alternatives": dict(sorted(syntax_with_alts.items())),
        "max_total_alternatives_per_query": max((row["total_alternatives"] for row in rows), default=0),
        "max_slot_count_per_query": max((row["slot_count"] for row in rows), default=0),
    }


def main():
    args = parse_args()
    files = query_files(args.queries_dir, args.limit)
    if not files:
        raise SystemExit(f"No .sql files found under {args.queries_dir}")

    ensure_database(args)
    initialize_schema(args)

    if args.isolated_per_query:
        per_query = run_isolated_measurement(args, files)
        psql_exit_code = 0
    else:
        measurement_sql = build_measurement_script(files, args.statement_timeout_ms)
        try:
            proc = run_command(
                psql_argv(args.pg_bin_dir, args.host, args.port, args.user, args.database) + ["-f", str(measurement_sql)],
                check=False,
            )
        finally:
            measurement_sql.unlink(missing_ok=True)
        per_query = parse_measurement_output(proc.stdout, files)
        psql_exit_code = proc.returncode

    report = {
        "database": args.database,
        "queries_dir": str(Path(args.queries_dir).resolve()),
        "schema_json": str(Path(args.schema_json).resolve()),
        "limit": args.limit,
        "statement_timeout_ms": args.statement_timeout_ms,
        "isolated_per_query": args.isolated_per_query,
        "summary": aggregate_report(per_query),
        "queries": per_query,
        "psql_exit_code": psql_exit_code,
    }

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))

    summary = report["summary"]
    print(f"queries={summary['query_count']}")
    print(f"errors={summary['error_count']}")
    print(f"queries_with_subquery_alternatives={summary['queries_with_subquery_alternatives']}")
    print(f"max_total_alternatives_per_query={summary['max_total_alternatives_per_query']}")
    print(f"slot_count_histogram={json.dumps(summary['slot_count_per_query_histogram'], sort_keys=True)}")
    print(f"total_alternatives_histogram={json.dumps(summary['total_alternatives_per_query_histogram'], sort_keys=True)}")
    print(f"alternatives_per_slot_histogram={json.dumps(summary['alternatives_per_slot_histogram'], sort_keys=True)}")
    print(f"report={output_path}")


if __name__ == "__main__":
    main()
