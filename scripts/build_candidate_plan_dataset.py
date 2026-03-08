#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build candidate_plans JSON records from Athena oracle-evaluation reports."
    )
    parser.add_argument("--pg-bin-dir", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--user", default=os.environ.get("USER", "postgres"))
    parser.add_argument("--database", required=True)
    parser.add_argument("--oracle-report", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument(
        "--fallback-queries-dir",
        default="",
        help="Directory to search by basename when oracle-report paths are no longer present.",
    )
    parser.add_argument("--statement-timeout-ms", type=int, default=5000)
    parser.add_argument("--max-candidates-per-query", type=int, default=0)
    parser.add_argument(
        "--label-source",
        choices=["oracle", "planner_min"],
        default="oracle",
        help="Use runtime oracle or planner-min candidate as the training label.",
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


def normalize_query_text(sql_text):
    sql_text = sql_text.strip()
    if sql_text.endswith(";"):
        sql_text = sql_text[:-1].rstrip()
    return sql_text


def run_explain_json(args, sql_text, *, force_idx):
    timeout_seconds = max(10, args.statement_timeout_ms / 1000.0 + 5.0)
    sql = "\n".join(
        [
            f"set statement_timeout = {args.statement_timeout_ms};",
            "set client_min_messages = notice;",
            "set enable_join_order_plans = on;",
            f"set athena_force_bound_candidate_idx = {force_idx};",
            f"explain (format json) {normalize_query_text(sql_text)};",
        ]
    ) + "\n"
    return run_command(
        psql_argv(args.pg_bin_dir, args.host, args.port, args.user, args.database, tuples_only=True),
        input_text=sql,
        timeout=timeout_seconds,
        check=False,
    )


def parse_explain_json_output(stdout_text):
    stdout_text = stdout_text.strip()
    if not stdout_text:
        return None
    data = json.loads(stdout_text)
    if isinstance(data, list) and data:
        return data[0]
    return data


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


def plan_to_candidate_dict(candidate_idx, candidate_meta, explain_obj):
    plan_root = explain_obj["Plan"]
    details = {
        "scan_methods": [],
        "join_methods": [],
        "subqueries": [],
        "other_operators": [],
    }
    collect_plan_details(plan_root, details)

    return {
        "candidate_id": candidate_idx,
        "startup_cost": plan_root.get("Startup Cost"),
        "total_cost": plan_root.get("Total Cost"),
        "est_rows": plan_root.get("Plan Rows"),
        "plan_width": plan_root.get("Plan Width"),
        "root_join_tree": render_join_tree(plan_root),
        "scan_methods": dedup_preserve_order(details["scan_methods"]),
        "join_methods": dedup_preserve_order(details["join_methods"]),
        "subqueries": details["subqueries"],
        "other_operators": dedup_preserve_order(details["other_operators"]),
        "bindings": candidate_meta.get("bindings", ""),
        "planner_candidate_cost": candidate_meta.get("total_cost"),
        "planner_candidate_rows": candidate_meta.get("rows"),
        "skeleton_candidate_id": candidate_meta.get("skeleton_candidate_id"),
        "bound_candidate_id": candidate_meta.get("bound_candidate_id"),
    }


def select_candidates(bound_candidates, label_candidate_id, max_candidates_per_query):
    selected = list(bound_candidates)
    if max_candidates_per_query > 0 and len(selected) > max_candidates_per_query:
        selected = sorted(selected, key=lambda row: (row["total_cost"], row["idx"]))[:max_candidates_per_query]
        if label_candidate_id is not None and not any(row["idx"] == label_candidate_id for row in selected):
            label_row = next((row for row in bound_candidates if row["idx"] == label_candidate_id), None)
            if label_row is not None:
                selected.append(label_row)
    return sorted(selected, key=lambda row: row["idx"])


def label_candidate_id(row, label_source):
    if label_source == "oracle":
        return row.get("oracle_candidate_idx")
    return row.get("planner_min_candidate_idx")


def resolve_sql_path(row_path, fallback_queries_dir):
    sql_path = Path(row_path)
    if sql_path.exists():
        return sql_path
    if fallback_queries_dir:
        fallback_path = Path(fallback_queries_dir) / sql_path.name
        if fallback_path.exists():
            return fallback_path
    return sql_path


def main():
    args = parse_args()
    report = json.loads(Path(args.oracle_report).read_text())
    output_rows = []

    for row in report.get("queries", []):
        sql_path = resolve_sql_path(row["path"], args.fallback_queries_dir)
        if not sql_path.exists():
            print(f"warning: SQL file not found for {row['path']}", file=sys.stderr)
            continue
        sql_text = sql_path.read_text(errors="ignore")
        label_id = label_candidate_id(row, args.label_source)
        selected_candidates = select_candidates(
            row.get("bound_candidates", []),
            label_id,
            args.max_candidates_per_query,
        )

        if not selected_candidates:
            continue

        serialized_candidates = []
        candidate_id_to_position = {}
        runtime_by_id = {
            candidate["idx"]: candidate.get("execution_time_ms")
            for candidate in row.get("oracle_candidates_evaluated", [])
            if candidate.get("execution_time_ms") is not None
        }

        for position, candidate in enumerate(selected_candidates):
            proc = run_explain_json(args, sql_text, force_idx=candidate["idx"])
            if proc.returncode != 0:
                continue

            explain_obj = parse_explain_json_output(proc.stdout)
            if explain_obj is None or "Plan" not in explain_obj:
                continue

            candidate_dict = plan_to_candidate_dict(candidate["idx"], candidate, explain_obj)
            if candidate["idx"] in runtime_by_id:
                candidate_dict["execution_time_ms"] = runtime_by_id[candidate["idx"]]
            serialized_candidates.append(candidate_dict)
            candidate_id_to_position[candidate["idx"]] = len(serialized_candidates) - 1

        if not serialized_candidates:
            continue

        best_position = candidate_id_to_position.get(label_id)
        if best_position is None:
            continue

        output_rows.append(
            {
                "sql": normalize_query_text(sql_text),
                "path": str(sql_path.resolve()),
                "db_name": report.get("database", args.database),
                "card_tb": "",
                "ndv": "",
                "main_value": "",
                "min_max": "",
                "hists": "",
                "candidate_plans": serialized_candidates,
                "best_candidate_idx": best_position,
                "best_candidate_id": label_id,
                "planner_min_candidate_id": row.get("planner_min_candidate_idx"),
                "oracle_candidate_id": row.get("oracle_candidate_idx"),
                "default_execution_time_ms": row.get("default_runtime", {}).get("execution_time_ms"),
                "planner_min_execution_time_ms": row.get("planner_min_runtime", {}).get("execution_time_ms"),
                "root_candidate_count": row.get("root_candidate_count"),
                "bound_candidate_count": row.get("bound_candidate_count"),
            }
        )

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_rows, indent=2))
    print(json.dumps({"record_count": len(output_rows)}, indent=2))
    print(output_path)


if __name__ == "__main__":
    main()
