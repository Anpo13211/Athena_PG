#!/usr/bin/env python3

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Copy only queries whose EXPLAIN plan still contains a Subquery Scan."
    )
    parser.add_argument("--pg-bin-dir", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def psql_argv(args):
    return [
        str(Path(args.pg_bin_dir) / "psql"),
        "-X",
        "-q",
        "-A",
        "-t",
        "-h",
        args.host,
        "-p",
        str(args.port),
        "-U",
        args.user,
        "-d",
        args.database,
        "-v",
        "ON_ERROR_STOP=1",
    ]


def contains_subquery_scan(plan_node):
    if plan_node.get("Node Type") == "Subquery Scan":
        return True
    return any(contains_subquery_scan(child) for child in plan_node.get("Plans", []))


def main():
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for old_file in output_dir.glob("*.sql"):
        old_file.unlink()

    total = 0
    valid = 0
    copied = 0
    invalid = []

    for sql_file in sorted(input_dir.glob("*.sql")):
        total += 1
        sql_text = sql_file.read_text().strip().rstrip(";")
        proc = subprocess.run(
            psql_argv(args),
            input=f"explain (format json) {sql_text};\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            invalid.append(sql_file.name)
            continue

        valid += 1
        explain_obj = json.loads(proc.stdout.strip())[0]
        if contains_subquery_scan(explain_obj["Plan"]):
            shutil.copy2(sql_file, output_dir / sql_file.name)
            copied += 1

    print(
        json.dumps(
            {
                "total": total,
                "valid": valid,
                "copied_with_subquery_scan": copied,
                "invalid": len(invalid),
                "sample_invalid": invalid[:10],
                "output_dir": str(output_dir.resolve()),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
