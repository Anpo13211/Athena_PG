#!/usr/bin/env python3

import argparse
import collections
import json
import statistics
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Summarize query families and candidate characteristics where default is not worse than the oracle."
    )
    parser.add_argument("--oracle-report", required=True)
    parser.add_argument("--top-n", type=int, default=20)
    return parser.parse_args()


def load_queries(path):
    obj = json.loads(Path(path).read_text())
    return obj["queries"]


def query_outer_family(query_name):
    tokens = query_name.replace(".sql", "").split("_")
    if "outer" not in tokens:
        return ""
    return "_".join(tokens[tokens.index("outer") + 1 :])


def root_join_op(candidate):
    serialized = candidate.get("serialized_plan") or {}
    tree = serialized.get("root_join_tree", "")
    return tree.split("(")[0] if tree else "UNKNOWN"


def main():
    args = parse_args()
    queries = load_queries(args.oracle_report)

    loss_rows = []
    improve_rows = []

    for query in queries:
        summary = query.get("summary") or {}
        delta = summary.get("default_minus_oracle_ms")
        if delta is None:
            continue

        oracle_idx = query.get("oracle_candidate_idx")
        candidates = {candidate["idx"]: candidate for candidate in query.get("bound_candidates", [])}
        oracle_candidate = candidates.get(oracle_idx, {})

        row = {
            "query_name": query["query_name"],
            "outer_family": query_outer_family(query["query_name"]),
            "default_minus_oracle_ms": delta,
            "default_ms": (query.get("default_runtime") or {}).get("execution_time_ms"),
            "oracle_ms": (query.get("oracle_runtime") or {}).get("execution_time_ms"),
            "planner_min_ms": (query.get("planner_min_runtime") or {}).get("execution_time_ms"),
            "root_candidate_count": query.get("root_candidate_count"),
            "bound_candidate_count": query.get("bound_candidate_count"),
            "binding_count": len(set(candidate.get("bindings") for candidate in query.get("bound_candidates", []))),
            "bindings": sorted(set(candidate.get("bindings") for candidate in query.get("bound_candidates", []))),
            "oracle_root_join": root_join_op(oracle_candidate),
        }
        (improve_rows if delta > 0 else loss_rows).append(row)

    def summarize(rows, label):
        print(f"[{label}] count={len(rows)}")
        if not rows:
            return
        for key in ["default_ms", "oracle_ms", "root_candidate_count", "bound_candidate_count", "binding_count"]:
            values = [row[key] for row in rows if row[key] is not None]
            print(
                f"  {key}: avg={sum(values)/len(values):.3f} median={statistics.median(values):.3f} min={min(values):.3f} max={max(values):.3f}"
            )
        family_counter = collections.Counter(row["outer_family"] for row in rows)
        root_counter = collections.Counter(row["oracle_root_join"] for row in rows)
        binding_counter = collections.Counter(tuple(row["bindings"]) for row in rows)
        print("  top outer families:")
        for family, count in family_counter.most_common(args.top_n):
            print(f"    {family}: {count}")
        print("  oracle root join operators:")
        for family, count in root_counter.most_common(args.top_n):
            print(f"    {family}: {count}")
        print("  most common binding sets:")
        for bindings, count in binding_counter.most_common(min(args.top_n, 10)):
            print(f"    {list(bindings)}: {count}")

    summarize(loss_rows, "default_not_worse")
    print()
    summarize(improve_rows, "oracle_better")
    print()
    print("[largest loss queries]")
    for row in sorted(loss_rows, key=lambda row: row["default_minus_oracle_ms"])[: args.top_n]:
        print(
            f"  {row['query_name']}: delta={row['default_minus_oracle_ms']:.3f}ms default={row['default_ms']:.3f}ms "
            f"oracle={row['oracle_ms']:.3f}ms bindings={row['bindings']}"
        )


if __name__ == "__main__":
    main()
