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


def terminate_database_backends(args):
    sql = (
        "select pg_terminate_backend(pid) "
        "from pg_stat_activity "
        f"where datname = '{args.database}' and pid <> pg_backend_pid();\n"
    )
    run_command(
        psql_argv(args.pg_bin_dir, args.host, args.port, args.user, "postgres"),
        input_text=sql,
        check=False,
    )


def normalize_query_text(sql_text):
    sql_text = sql_text.strip()
    if sql_text.endswith(";"):
        sql_text = sql_text[:-1].rstrip()
    return sql_text


def run_explain_json(args, sql_text, *, candidate):
    timeout_seconds = max(4, args.statement_timeout_ms / 1000.0 + 1.0)
    sql = "\n".join(
        [
            f"set statement_timeout = {args.statement_timeout_ms};",
            "set client_min_messages = notice;",
            "set enable_join_order_plans = on;",
            f"set athena_force_bound_candidate_idx = {candidate.get('local_idx', candidate['idx'])};",
            "set athena_disable_simple_from_subquery_pullup = "
            + ("on;" if candidate.get("representation_mode") == "no_pullup" else "off;"),
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
        "representation_mode": candidate_meta.get("representation_mode", "flattened"),
        "local_idx": candidate_meta.get("local_idx", candidate_idx),
        "is_default_baseline": candidate_meta.get("is_default_baseline", False),
        "is_exact_default_baseline": candidate_meta.get("is_exact_default_baseline", False),
    }


def serialized_candidate_dict(candidate_idx, candidate_meta):
    candidate_dict = dict(candidate_meta.get("serialized_plan", {}))
    candidate_dict["candidate_id"] = candidate_idx
    candidate_dict["bindings"] = candidate_meta.get("bindings", "")
    candidate_dict["planner_candidate_cost"] = candidate_meta.get("total_cost")
    candidate_dict["planner_candidate_rows"] = candidate_meta.get("rows")
    candidate_dict["skeleton_candidate_id"] = candidate_meta.get("skeleton_candidate_id")
    candidate_dict["bound_candidate_id"] = candidate_meta.get("bound_candidate_id")
    candidate_dict["representation_mode"] = candidate_meta.get("representation_mode", "flattened")
    candidate_dict["local_idx"] = candidate_meta.get("local_idx", candidate_idx)
    candidate_dict["is_default_baseline"] = candidate_meta.get("is_default_baseline", False)
    candidate_dict["is_exact_default_baseline"] = candidate_meta.get("is_exact_default_baseline", False)
    return candidate_dict


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
        return sorted(candidates, key=lambda row: (row["total_cost"], row["idx"]))

    ordered = sorted(candidates, key=lambda row: (row["total_cost"], row["idx"]))
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
            cheapest_by_skeleton.setdefault(candidate_skeleton_key(candidate), candidate)
        for candidate in sorted(
            cheapest_by_skeleton.values(),
            key=lambda row: (row["total_cost"], row["idx"]),
        ):
            add_candidate(candidate)

    if len(selected) < limit:
        for candidate in ordered:
            add_candidate(candidate)

    return sorted(selected, key=lambda row: row["idx"])


def select_candidates(bound_candidates, label_candidate_id, max_candidates_per_query):
    selected = list(bound_candidates)
    protected_default = next((row for row in bound_candidates if row.get("is_default_baseline")), None)
    if max_candidates_per_query > 0 and len(selected) > max_candidates_per_query:
        label_row = next((row for row in bound_candidates if row["idx"] == label_candidate_id), None)
        protected = []
        if protected_default is not None:
            protected.append(protected_default)
        if label_row is not None and all(row["idx"] != label_row["idx"] for row in protected):
            protected.append(label_row)

        remaining = [
            row
            for row in sorted(bound_candidates, key=lambda row: (row["total_cost"], row["idx"]))
            if row["idx"] not in {candidate["idx"] for candidate in protected}
        ]

        selected = list(protected)

        def add_first_match(candidates):
            nonlocal selected
            result = select_binding_aware_candidates(
                candidates,
                max_candidates_per_query,
                preselected=selected,
            )
            if len(result) > len(selected):
                selected = result

        if len(selected) < max_candidates_per_query:
            add_first_match(remaining[:1])

        if len(selected) < max_candidates_per_query:
            sorted_merge_candidates = [
                candidate for candidate in remaining if candidate_is_sorted_merge_friendly(candidate)
            ]
            add_first_match(sorted_merge_candidates[:1])

        if len(selected) < max_candidates_per_query:
            selected = select_binding_aware_candidates(
                remaining,
                max_candidates_per_query,
                preselected=selected,
            )

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

        terminate_database_backends(args)
        for position, candidate in enumerate(selected_candidates):
            if "serialized_plan" in candidate:
                candidate_dict = serialized_candidate_dict(candidate["idx"], candidate)
            else:
                try:
                    proc = run_explain_json(args, sql_text, candidate=candidate)
                except subprocess.TimeoutExpired:
                    terminate_database_backends(args)
                    continue
                if proc.returncode != 0:
                    terminate_database_backends(args)
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
        terminate_database_backends(args)

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_rows, indent=2))
    print(json.dumps({"record_count": len(output_rows)}, indent=2))
    print(output_path)


if __name__ == "__main__":
    main()
