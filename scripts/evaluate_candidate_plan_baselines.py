#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate cheap non-LLM baselines on candidate_plans datasets."
    )
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", default="")
    return parser.parse_args()


def planner_cost_min_idx(candidate_plans):
    best_idx = None
    best_cost = None
    for idx, candidate in enumerate(candidate_plans):
        cost = candidate.get("total_cost")
        if cost is None:
            continue
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_idx = idx
    return best_idx


def structure_first_idx(candidate_plans):
    best_idx = None
    best_key = None
    for idx, candidate in enumerate(candidate_plans):
        join_methods = candidate.get("join_methods") or []
        other_operators = candidate.get("other_operators") or []
        nested_loop_count = sum(1 for method in join_methods if str(method).startswith("Nested Loop:"))
        sort_like_count = sum(1 for op in other_operators if op in {"Sort", "Incremental Sort", "Gather Merge"})
        materialize_count = sum(1 for op in other_operators if op in {"Materialize", "Memoize"})
        total_cost = candidate.get("total_cost")
        key = (
            nested_loop_count + sort_like_count + materialize_count,
            nested_loop_count,
            sort_like_count,
            float("inf") if total_cost is None else total_cost,
            idx,
        )
        if best_key is None or key < best_key:
            best_key = key
            best_idx = idx
    return best_idx


def execution_time_at(record, idx):
    candidate = record["candidate_plans"][idx]
    return candidate.get("execution_time_ms")


def summarize(records):
    planner_correct = 0
    heuristic_correct = 0
    planner_runtime_total = 0.0
    heuristic_runtime_total = 0.0
    oracle_runtime_total = 0.0
    runtime_comparable_queries = 0
    results = []

    for record in records:
        oracle_idx = record["best_candidate_idx"]
        planner_idx = planner_cost_min_idx(record["candidate_plans"])
        heuristic_idx = structure_first_idx(record["candidate_plans"])

        if planner_idx == oracle_idx:
            planner_correct += 1
        if heuristic_idx == oracle_idx:
            heuristic_correct += 1

        oracle_runtime = execution_time_at(record, oracle_idx)
        planner_runtime = execution_time_at(record, planner_idx) if planner_idx is not None else None
        heuristic_runtime = execution_time_at(record, heuristic_idx) if heuristic_idx is not None else None

        if oracle_runtime is not None and planner_runtime is not None and heuristic_runtime is not None:
            runtime_comparable_queries += 1
            oracle_runtime_total += oracle_runtime
            planner_runtime_total += planner_runtime
            heuristic_runtime_total += heuristic_runtime

        results.append(
            {
                "path": record.get("path"),
                "oracle_idx": oracle_idx,
                "planner_cost_min_idx": planner_idx,
                "structure_first_idx": heuristic_idx,
                "oracle_runtime_ms": oracle_runtime,
                "planner_runtime_ms": planner_runtime,
                "heuristic_runtime_ms": heuristic_runtime,
            }
        )

    query_count = len(records)
    summary = {
        "query_count": query_count,
        "planner_cost_min_accuracy": planner_correct / query_count if query_count else 0.0,
        "structure_first_accuracy": heuristic_correct / query_count if query_count else 0.0,
        "runtime_comparable_queries": runtime_comparable_queries,
    }
    if runtime_comparable_queries:
        summary["planner_cost_min_avg_runtime_ms"] = planner_runtime_total / runtime_comparable_queries
        summary["structure_first_avg_runtime_ms"] = heuristic_runtime_total / runtime_comparable_queries
        summary["oracle_avg_runtime_ms"] = oracle_runtime_total / runtime_comparable_queries

    return {"summary": summary, "queries": results}


def main():
    args = parse_args()
    records = json.loads(Path(args.input_json).read_text())
    report = summarize(records)

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2))

    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
