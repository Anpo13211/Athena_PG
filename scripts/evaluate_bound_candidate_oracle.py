#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
import subprocess
import statistics
from pathlib import Path

SCAN_NODE_TYPES = {
    "Seq Scan",
    "Sample Scan",
    "Index Scan",
    "Index Only Scan",
    "Bitmap Heap Scan",
    "Bitmap Index Scan",
    "Tid Scan",
    "Tid Range Scan",
    "Subquery Scan",
    "Function Scan",
    "Values Scan",
    "Table Function Scan",
    "CTE Scan",
    "Named Tuplestore Scan",
    "WorkTable Scan",
    "Foreign Scan",
    "Custom Scan",
}

JOIN_NODE_TYPES = {
    "Nested Loop",
    "Hash Join",
    "Merge Join",
}

OTHER_OPERATOR_TYPES = {
    "Sort",
    "Incremental Sort",
    "Materialize",
    "Memoize",
    "Gather",
    "Gather Merge",
    "Append",
    "Merge Append",
    "Unique",
    "Aggregate",
    "GroupAggregate",
    "HashAggregate",
    "Limit",
    "LockRows",
    "Result",
    "Hash",
    "SetOp",
    "WindowAgg",
}

PASSTHROUGH_TYPES = {
    "Hash",
    "Sort",
    "Incremental Sort",
    "Materialize",
    "Memoize",
    "Unique",
    "Aggregate",
    "GroupAggregate",
    "HashAggregate",
    "Limit",
    "LockRows",
    "Result",
    "Gather",
    "Gather Merge",
}


ROOT_COUNT_RE = re.compile(r"Athena root candidate count: (\d+)")
BOUND_COUNT_RE = re.compile(r"Athena bound candidate count: (\d+)")
BOUND_CANDIDATE_RE = re.compile(
    r"Athena bound candidate idx=(\d+) id=(\d+) skeleton=(-?\d+) default=(true|false) total_cost=([0-9.eE+-]+) rows=([0-9.eE+-]+) bindings=(.*)"
)
BOUND_SUMMARY_RE = re.compile(
    r"Athena bound candidate summary idx=(\d+) json=(\{.*\})"
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
    parser.add_argument("--schema-json", default="")
    parser.add_argument(
        "--skip-schema-init",
        action="store_true",
        help="Use an existing populated database as-is; do not create tables from schema-json.",
    )
    parser.add_argument("--queries-dir", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from an existing output JSON by skipping already-recorded queries.",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--statement-timeout-ms", type=int, default=5000)
    parser.add_argument("--max-oracle-candidates", type=int, default=0)
    parser.add_argument(
        "--athena-max-distinct-bindings-per-skeleton",
        type=int,
        default=1,
        help="Keep up to this many binding-distinct completed candidates per skeleton.",
    )
    parser.add_argument(
        "--athena-dual-simple-from-subqueries",
        action="store_true",
        help="Collect both flattened and no-pullup representations for eligible simple FROM-subqueries.",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="Number of EXPLAIN ANALYZE repetitions per runtime measurement.",
    )
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


def ensure_existing_database(args):
    postgres_argv = psql_argv(args.pg_bin_dir, args.host, args.port, args.user, "postgres")
    check_db = run_command(
        postgres_argv + ["-Atqc", f"select 1 from pg_database where datname = '{args.database}'"],
        check=True,
    )
    if check_db.stdout.strip() != "1":
        raise SystemExit(
            f"Database {args.database} does not exist; create/load it first or run without --skip-schema-init."
        )


def terminate_database_backends(args):
    postgres_argv = psql_argv(args.pg_bin_dir, args.host, args.port, args.user, "postgres")
    sql = (
        "select pg_terminate_backend(pid) "
        "from pg_stat_activity "
        f"where datname = '{args.database}' and pid <> pg_backend_pid();\n"
    )
    run_command(postgres_argv, input_text=sql, check=False)


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


def get_node_display_name(node):
    alias = node.get("Alias")
    relname = node.get("Relation Name")
    subplan = node.get("Subplan Name")
    if alias:
        return alias
    if relname:
        return relname
    if subplan:
        return subplan
    return node.get("Node Type", "Unknown")


def render_join_tree(node):
    node_type = node.get("Node Type", "Unknown")
    children = node.get("Plans", [])

    if node_type in JOIN_NODE_TYPES and len(children) >= 2:
        left = render_join_tree(children[0])
        right = render_join_tree(children[1])
        return f"{node_type}({left}, {right})"

    if node_type == "Subquery Scan":
        return get_node_display_name(node)

    if node_type in SCAN_NODE_TYPES:
        return get_node_display_name(node)

    if node_type in PASSTHROUGH_TYPES and children:
        return render_join_tree(children[0])

    if len(children) == 1:
        return render_join_tree(children[0])

    if len(children) > 1:
        rendered_children = ", ".join(render_join_tree(child) for child in children)
        return f"{node_type}({rendered_children})"

    return get_node_display_name(node)


def dedup_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def collect_plan_details(node, details):
    node_type = node.get("Node Type", "Unknown")
    children = node.get("Plans", [])

    if node_type == "Subquery Scan":
        child = children[0] if children else None
        details["subqueries"].append(
            {
                "name": get_node_display_name(node),
                "est_rows": node.get("Plan Rows"),
                "join_tree": render_join_tree(child) if child else "None",
            }
        )

    if node_type in JOIN_NODE_TYPES:
        details["join_methods"].append(f"{node_type}: {render_join_tree(node)}")
    elif node_type in SCAN_NODE_TYPES:
        details["scan_methods"].append(f"{get_node_display_name(node)}: {node_type}")
    elif node_type in OTHER_OPERATOR_TYPES:
        details["other_operators"].append(node_type)

    for child in children:
        collect_plan_details(child, details)


def serialize_explain_plan(explain_obj):
    plan_root = explain_obj["Plan"]
    details = {
        "scan_methods": [],
        "join_methods": [],
        "subqueries": [],
        "other_operators": [],
    }
    collect_plan_details(plan_root, details)
    return {
        "startup_cost": plan_root.get("Startup Cost"),
        "total_cost": plan_root.get("Total Cost"),
        "est_rows": plan_root.get("Plan Rows"),
        "plan_width": plan_root.get("Plan Width"),
        "root_join_tree": render_join_tree(plan_root),
        "scan_methods": dedup_preserve_order(details["scan_methods"]),
        "join_methods": dedup_preserve_order(details["join_methods"]),
        "subqueries": details["subqueries"],
        "other_operators": dedup_preserve_order(details["other_operators"]),
    }


def run_explain_json(args, sql_text, *, enable_join_order_plans, analyze, force_idx=None,
                     disable_simple_from_subquery_pullup=False, debug=False):
    timeout_seconds = max(4, args.statement_timeout_ms / 1000.0 + 1.0)
    settings = [
        f"set statement_timeout = {args.statement_timeout_ms};",
        "set client_min_messages = debug1;" if debug else "set client_min_messages = notice;",
        f"set enable_join_order_plans = {'on' if enable_join_order_plans else 'off'};",
        f"set athena_force_bound_candidate_idx = {force_idx if force_idx is not None else -1};",
        f"set athena_max_distinct_bindings_per_skeleton = {args.athena_max_distinct_bindings_per_skeleton};",
        "set athena_disable_simple_from_subquery_pullup = "
        + ("on;" if disable_simple_from_subquery_pullup else "off;"),
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


def collect_bound_candidates_for_mode(args, sql_text, *, representation_mode, disable_simple_from_subquery_pullup):
    try:
        proc = run_explain_json(
            args,
            sql_text,
            enable_join_order_plans=True,
            analyze=False,
            force_idx=None,
            disable_simple_from_subquery_pullup=disable_simple_from_subquery_pullup,
            debug=True,
        )
    except subprocess.TimeoutExpired:
        terminate_database_backends(args)
        return {
            "returncode": 124,
            "stderr": "psql timeout during bound-candidate collection",
            "stdout": "",
            "root_candidate_count": None,
            "bound_candidate_count": None,
            "candidates": [],
        }
    candidates = []
    root_count = None
    bound_count = None
    serialized_by_idx = {}

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
                    "local_idx": int(match.group(1)),
                    "bound_candidate_id": int(match.group(2)),
                    "skeleton_candidate_id": int(match.group(3)),
                    "is_default_baseline": match.group(4) == "true",
                    "total_cost": float(match.group(5)),
                    "rows": float(match.group(6)),
                    "bindings": match.group(7).strip(),
                    "representation_mode": representation_mode,
                }
            )
        match = BOUND_SUMMARY_RE.search(line)
        if match:
            try:
                serialized_by_idx[int(match.group(1))] = json.loads(match.group(2))
            except json.JSONDecodeError:
                continue

    for candidate in candidates:
        if candidate["idx"] in serialized_by_idx:
            candidate["serialized_plan"] = serialized_by_idx[candidate["idx"]]

    if proc.returncode != 0:
        terminate_database_backends(args)

    return {
        "returncode": proc.returncode,
        "stderr": proc.stderr,
        "stdout": proc.stdout,
        "root_candidate_count": root_count,
        "bound_candidate_count": bound_count,
        "candidates": sorted(candidates, key=lambda row: row["idx"]),
    }


def collect_bound_candidates(args, sql_text):
    modes = [("flattened", False)]
    if args.athena_dual_simple_from_subqueries:
        modes.append(("no_pullup", True))

    per_mode = []
    for representation_mode, disable_pullup in modes:
        mode_info = collect_bound_candidates_for_mode(
            args,
            sql_text,
            representation_mode=representation_mode,
            disable_simple_from_subquery_pullup=disable_pullup,
        )
        per_mode.append((representation_mode, mode_info))

    merged_candidates = []
    next_idx = 0
    root_total = 0
    bound_total = 0
    for representation_mode, mode_info in per_mode:
        root_total += mode_info["root_candidate_count"] or 0
        bound_total += mode_info["bound_candidate_count"] or 0
        for candidate in mode_info["candidates"]:
            merged = dict(candidate)
            merged["idx"] = next_idx
            merged["representation_mode"] = representation_mode
            merged["is_default_baseline"] = False
            next_idx += 1
            merged_candidates.append(merged)

    combined_returncode = 0
    stderr_parts = []
    stdout_parts = []
    for representation_mode, mode_info in per_mode:
        if mode_info["returncode"] != 0 and combined_returncode == 0:
            combined_returncode = mode_info["returncode"]
        if mode_info["stderr"]:
            stderr_parts.append(f"[{representation_mode}]")
            stderr_parts.append(mode_info["stderr"])
        if mode_info["stdout"]:
            stdout_parts.append(mode_info["stdout"])

    return {
        "returncode": combined_returncode,
        "stderr": "\n".join(stderr_parts),
        "stdout": "\n".join(stdout_parts),
        "root_candidate_count": root_total,
        "bound_candidate_count": bound_total,
        "root_candidate_count_by_mode": {
            representation_mode: mode_info["root_candidate_count"]
            for representation_mode, mode_info in per_mode
        },
        "bound_candidate_count_by_mode": {
            representation_mode: mode_info["bound_candidate_count"]
            for representation_mode, mode_info in per_mode
        },
        "candidates": merged_candidates,
    }


def collect_exact_default_candidate(args, sql_text, next_idx):
    try:
        proc = run_explain_json(
            args,
            sql_text,
            enable_join_order_plans=False,
            analyze=False,
            force_idx=None,
            disable_simple_from_subquery_pullup=False,
            debug=False,
        )
    except subprocess.TimeoutExpired:
        terminate_database_backends(args)
        return None

    if proc.returncode != 0:
        terminate_database_backends(args)
        return None

    explain_obj = parse_explain_json_output(proc.stdout)
    if explain_obj is None or "Plan" not in explain_obj:
        return None

    serialized_plan = serialize_explain_plan(explain_obj)
    return {
        "idx": next_idx,
        "local_idx": -1,
        "bound_candidate_id": -1,
        "skeleton_candidate_id": -1,
        "is_default_baseline": True,
        "is_exact_default_baseline": True,
        "total_cost": serialized_plan.get("total_cost"),
        "rows": serialized_plan.get("est_rows"),
        "bindings": "[]",
        "representation_mode": "default_exact",
        "serialized_plan": serialized_plan,
    }


def evaluate_runtime(args, sql_text, *, enable_join_order_plans, force_candidate=None):
    result = {
        "returncode": 0,
        "stderr": [],
        "planning_time_ms": None,
        "execution_time_ms": None,
        "planning_time_runs_ms": [],
        "execution_time_runs_ms": [],
    }

    for _ in range(max(1, args.repetitions)):
        try:
            proc = run_explain_json(
                args,
                sql_text,
                enable_join_order_plans=enable_join_order_plans,
                analyze=True,
                force_idx=force_candidate["local_idx"] if force_candidate is not None else None,
                disable_simple_from_subquery_pullup=(
                    force_candidate is not None and
                    force_candidate.get("representation_mode") == "no_pullup"
                ),
                debug=False,
            )
        except subprocess.TimeoutExpired:
            terminate_database_backends(args)
            result["returncode"] = 124
            result["stderr"] = ["psql timeout during EXPLAIN ANALYZE"]
            return result
        if proc.returncode != 0:
            terminate_database_backends(args)
            result["returncode"] = proc.returncode
            result["stderr"] = proc.stderr.strip().splitlines()[:10]
            return result

        explain_obj = parse_explain_json_output(proc.stdout)
        if explain_obj is None:
            result["stderr"] = ["missing EXPLAIN JSON output"]
            result["returncode"] = 1
            return result

        planning_time = explain_obj.get("Planning Time")
        execution_time = explain_obj.get("Execution Time")
        if planning_time is not None:
            result["planning_time_runs_ms"].append(planning_time)
        if execution_time is not None:
            result["execution_time_runs_ms"].append(execution_time)

    if result["planning_time_runs_ms"]:
        result["planning_time_ms"] = statistics.median(result["planning_time_runs_ms"])
    if result["execution_time_runs_ms"]:
        result["execution_time_ms"] = statistics.median(result["execution_time_runs_ms"])
    return result


def candidate_binding_key(candidate):
    return (candidate.get("representation_mode", "flattened"), candidate.get("bindings") or "")


def candidate_skeleton_key(candidate):
    return (candidate.get("representation_mode", "flattened"),
            candidate.get("skeleton_candidate_id"))


def candidate_plan_signature(candidate):
    serialized = candidate.get("serialized_plan")
    if serialized is not None:
        return json.dumps(serialized, sort_keys=True)
    return json.dumps(
        {
            "representation_mode": candidate.get("representation_mode", "flattened"),
            "bindings": candidate.get("bindings", ""),
            "skeleton_candidate_id": candidate.get("skeleton_candidate_id"),
            "total_cost": candidate.get("total_cost"),
        },
        sort_keys=True,
    )


def candidate_is_sorted_merge_friendly(candidate):
    serialized = candidate.get("serialized_plan") or {}
    root_join_tree = serialized.get("root_join_tree", "")
    join_methods = serialized.get("join_methods") or []
    other_ops = set(serialized.get("other_operators") or [])
    return (
        root_join_tree.startswith("Merge Join")
        or any(method.startswith("Merge Join:") for method in join_methods)
        or "Sort" in other_ops
        or "Gather Merge" in other_ops
        or "Incremental Sort" in other_ops
    )


def select_binding_aware_candidates(candidates, limit, *, preselected=None):
    if limit <= 0:
        return sorted(candidates, key=lambda candidate: (candidate["total_cost"], candidate["idx"]))

    ordered = sorted(candidates, key=lambda candidate: (candidate["total_cost"], candidate["idx"]))
    selected = list(preselected or [])
    selected_ids = {candidate["idx"] for candidate in selected}
    selected_skeletons = {candidate_skeleton_key(candidate) for candidate in selected}
    selected_signatures = {candidate_plan_signature(candidate) for candidate in selected}

    def add_candidate(candidate):
        if (
            candidate["idx"] in selected_ids
            or len(selected) >= limit
            or candidate_plan_signature(candidate) in selected_signatures
        ):
            return False
        selected.append(candidate)
        selected_ids.add(candidate["idx"])
        selected_skeletons.add(candidate_skeleton_key(candidate))
        selected_signatures.add(candidate_plan_signature(candidate))
        return True

    by_binding = {}
    for candidate in ordered:
        by_binding.setdefault(candidate_binding_key(candidate), []).append(candidate)

    for binding_key in sorted(
        by_binding,
        key=lambda key: (by_binding[key][0]["total_cost"], by_binding[key][0]["idx"]),
    ):
        preferred = None
        for candidate in by_binding[binding_key]:
            if candidate_skeleton_key(candidate) not in selected_skeletons:
                preferred = candidate
                break
        if preferred is None:
            preferred = by_binding[binding_key][0]
        add_candidate(preferred)

    if len(selected) < limit:
        cheapest_by_skeleton = {}
        for candidate in ordered:
            skeleton_key = candidate_skeleton_key(candidate)
            cheapest_by_skeleton.setdefault(skeleton_key, candidate)
        for candidate in sorted(
            cheapest_by_skeleton.values(),
            key=lambda candidate: (candidate["total_cost"], candidate["idx"]),
        ):
            add_candidate(candidate)

    if len(selected) < limit:
        for candidate in ordered:
            add_candidate(candidate)

    return sorted(selected, key=lambda candidate: candidate["idx"])


def select_oracle_candidates(bound_candidates, max_oracle_candidates):
    if max_oracle_candidates <= 0:
        return sorted(bound_candidates, key=lambda candidate: candidate["idx"])

    protected_default = next(
        (candidate for candidate in bound_candidates if candidate.get("is_default_baseline")),
        None,
    )
    selected = []
    if protected_default is not None:
        selected.append(protected_default)

    non_default = [
        candidate
        for candidate in sorted(bound_candidates, key=lambda candidate: (candidate["total_cost"], candidate["idx"]))
        if not candidate.get("is_default_baseline")
    ]

    def add_first_match(candidates):
        nonlocal selected
        result = select_binding_aware_candidates(candidates, len(selected) + 1, preselected=selected)
        if len(result) > len(selected):
            selected = result

    if len(selected) < max_oracle_candidates:
        add_first_match(non_default[:1])

    if len(selected) < max_oracle_candidates:
        sorted_merge_candidates = [
            candidate for candidate in non_default if candidate_is_sorted_merge_friendly(candidate)
        ]
        add_first_match(sorted_merge_candidates[:1])

    if len(selected) < max_oracle_candidates:
        selected = select_binding_aware_candidates(non_default, max_oracle_candidates, preselected=selected)

    return sorted(selected, key=lambda candidate: candidate["idx"])


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


def write_report(output_path, report, *, pretty=True):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        output_path.write_text(json.dumps(report, indent=2))
    else:
        output_path.write_text(json.dumps(report))


def recompute_report_counters(report):
    queries = report.get("queries", [])
    default_runtime_success_count = 0
    bound_collection_success_count = 0
    oracle_eligible_count = 0
    oracle_better = 0
    default_better = 0
    planner_min_matches_oracle = 0

    for row in queries:
        default_runtime_success = bool(row.get("default_runtime_success"))
        bound_collection_success = bool(row.get("bound_collection_success"))
        oracle_eligible = bool(row.get("oracle_eligible"))

        if default_runtime_success:
            default_runtime_success_count += 1

        # Match the original online aggregation semantics in main():
        # bound_collection_success_count and oracle_eligible_count are only
        # incremented after a successful default runtime.
        if default_runtime_success and bound_collection_success:
            bound_collection_success_count += 1
        if default_runtime_success and oracle_eligible:
            oracle_eligible_count += 1

        default_runtime = row.get("default_runtime", {})
        planner_min_runtime = row.get("planner_min_runtime", {})
        oracle_runtime = row.get("oracle_runtime", {})

        default_ms = default_runtime.get("execution_time_ms")
        planner_min_ms = planner_min_runtime.get("execution_time_ms")
        oracle_ms = oracle_runtime.get("execution_time_ms")

        if default_ms is not None and oracle_ms is not None:
            if oracle_ms < default_ms:
                oracle_better += 1
            else:
                default_better += 1

        if planner_min_ms is not None and oracle_ms is not None and planner_min_ms == oracle_ms:
            planner_min_matches_oracle += 1

    report["summary"] = {
        "query_count": len(queries),
        "default_runtime_success_count": default_runtime_success_count,
        "bound_collection_success_count": bound_collection_success_count,
        "oracle_eligible_count": oracle_eligible_count,
        "oracle_better_than_default_count": oracle_better,
        "default_not_worse_than_oracle_count": default_better,
        "planner_min_equal_oracle_count": planner_min_matches_oracle,
    }
    return report["summary"]


def main():
    args = parse_args()
    if args.recreate_db and args.skip_schema_init:
        raise SystemExit("--recreate-db and --skip-schema-init cannot be used together.")
    if not args.skip_schema_init and not args.schema_json:
        raise SystemExit("--schema-json is required unless --skip-schema-init is used.")

    files = query_files(args.queries_dir, args.limit)
    if not files:
        raise SystemExit(f"No .sql files found under {args.queries_dir}")

    if args.skip_schema_init:
        ensure_existing_database(args)
    else:
        ensure_database(args)
        initialize_schema(args)

    report = {
        "database": args.database,
        "queries_dir": str(Path(args.queries_dir).resolve()),
        "schema_json": str(Path(args.schema_json).resolve()) if args.schema_json else "",
        "skip_schema_init": args.skip_schema_init,
        "statement_timeout_ms": args.statement_timeout_ms,
        "max_oracle_candidates": args.max_oracle_candidates,
        "athena_max_distinct_bindings_per_skeleton": args.athena_max_distinct_bindings_per_skeleton,
        "athena_dual_simple_from_subqueries": args.athena_dual_simple_from_subqueries,
        "queries": [],
    }
    processed_names = set()

    if args.resume:
        output_path = Path(args.output_json)
        if output_path.exists():
            report = json.loads(output_path.read_text())
            summary = recompute_report_counters(report)
            processed_names = {
                row.get("query_name")
                for row in report.get("queries", [])
                if row.get("query_name")
            }
            files = [sql_file for sql_file in files if sql_file.name not in processed_names]
            print(
                f"Resuming from {output_path} with {len(processed_names)} completed queries; "
                f"{len(files)} remaining.",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(
                f"--resume requested but {output_path} does not exist; starting fresh.",
                file=sys.stderr,
                flush=True,
            )

    summary = recompute_report_counters(report)
    default_better = summary["default_not_worse_than_oracle_count"]
    oracle_better = summary["oracle_better_than_default_count"]
    planner_min_matches_oracle = summary["planner_min_equal_oracle_count"]
    default_runtime_success_count = summary["default_runtime_success_count"]
    bound_collection_success_count = summary["bound_collection_success_count"]
    oracle_eligible_count = summary["oracle_eligible_count"]
    total_queries = len(report["queries"]) + len(files)

    for sql_file in files:
        total_index = len(report["queries"]) + 1
        print(f"[{total_index}/{total_queries}] {sql_file.name}", file=sys.stderr, flush=True)
        terminate_database_backends(args)
        sql_text = sql_file.read_text(errors="ignore")
        row = {
            "query_name": sql_file.name,
            "path": str(sql_file.resolve()),
            "default_runtime": evaluate_runtime(
                args, sql_text, enable_join_order_plans=False, force_candidate=None
            ),
        }

        bound_info = collect_bound_candidates(args, sql_text)
        row["root_candidate_count"] = bound_info["root_candidate_count"]
        row["bound_candidate_count"] = bound_info["bound_candidate_count"]
        row["root_candidate_count_by_mode"] = bound_info.get("root_candidate_count_by_mode", {})
        row["bound_candidate_count_by_mode"] = bound_info.get("bound_candidate_count_by_mode", {})
        row["default_runtime_success"] = row["default_runtime"].get("execution_time_ms") is not None
        row["bound_collection_success"] = bound_info["returncode"] == 0 and bool(bound_info["candidates"])
        row["oracle_eligible"] = False

        if row["default_runtime_success"]:
            default_runtime_success_count += 1
            if args.write_valid_queries_dir:
                write_query_copy(args.write_valid_queries_dir, sql_file, sql_text)

        if bound_info["returncode"] != 0 or not bound_info["candidates"]:
            row["bound_candidates"] = bound_info["candidates"]
            row["planner_min_runtime"] = {"returncode": 1, "stderr": ["failed to collect bound candidates"]}
            row["oracle_runtime"] = {"returncode": 1, "stderr": ["failed to collect bound candidates"]}
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
            write_report(args.output_json, report)
            continue

        if not row["default_runtime_success"]:
            row["bound_candidates"] = bound_info["candidates"]
            row["planner_min_runtime"] = {"returncode": 1, "stderr": ["skipped because default runtime failed"]}
            row["oracle_runtime"] = {"returncode": 1, "stderr": ["skipped because default runtime failed"]}
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
            write_report(args.output_json, report)
            continue

        bound_collection_success_count += 1

        exact_default = collect_exact_default_candidate(
            args,
            sql_text,
            max((candidate["idx"] for candidate in bound_info["candidates"]), default=-1) + 1,
        )
        if exact_default is not None:
            bound_info["candidates"].append(exact_default)

        row["bound_candidates"] = sorted(bound_info["candidates"], key=lambda candidate: candidate["idx"])

        planner_min = min(row["bound_candidates"], key=lambda candidate: candidate["total_cost"])
        row["planner_min_candidate_idx"] = planner_min["idx"]
        if planner_min.get("is_exact_default_baseline"):
            row["planner_min_runtime"] = dict(row["default_runtime"])
        else:
            row["planner_min_runtime"] = evaluate_runtime(
                args, sql_text, enable_join_order_plans=True, force_candidate=planner_min
            )
        runtime_by_idx = {planner_min["idx"]: dict(row["planner_min_runtime"])}
        if exact_default is not None:
            runtime_by_idx[exact_default["idx"]] = dict(row["default_runtime"])

        oracle_candidates = select_oracle_candidates(
            row["bound_candidates"], args.max_oracle_candidates
        )

        runtime_results = []
        for candidate in oracle_candidates:
            runtime = runtime_by_idx.get(candidate["idx"])
            if runtime is None:
                if candidate.get("is_exact_default_baseline"):
                    runtime = dict(row["default_runtime"])
                else:
                    runtime = evaluate_runtime(
                        args, sql_text, enable_join_order_plans=True, force_candidate=candidate
                    )
                runtime_by_idx[candidate["idx"]] = dict(runtime)
            else:
                runtime = dict(runtime)
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
        terminate_database_backends(args)
        report["summary"] = {
            "query_count": len(report["queries"]),
            "default_runtime_success_count": default_runtime_success_count,
            "bound_collection_success_count": bound_collection_success_count,
            "oracle_eligible_count": oracle_eligible_count,
            "oracle_better_than_default_count": oracle_better,
            "default_not_worse_than_oracle_count": default_better,
            "planner_min_equal_oracle_count": planner_min_matches_oracle,
        }
        write_report(args.output_json, report)

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
    write_report(output_path, report)
    print(json.dumps(report["summary"], indent=2))
    print(output_path)


if __name__ == "__main__":
    main()
