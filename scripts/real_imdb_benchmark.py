#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ZERO_SHOT_ROOT = REPO_ROOT.parent / "thesis" / "zero-shot"
DEFAULT_DATASET_DIR = DEFAULT_ZERO_SHOT_ROOT / "cross_db_benchmark" / "datasets" / "imdb"
DEFAULT_WORKLOAD_DIR = REPO_ROOT / "tmp" / "generated_job_v1_subqueryscan_queries"
DEFAULT_OUTPUT = REPO_ROOT / "tmp" / "oracle_generated_job_v1_195q_real_imdb_v1.json"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Load local IMDB real data into the PG18.1 Athena fork and print/run a real-data oracle evaluation."
    )
    parser.add_argument("--pg-bin-dir", default=str(REPO_ROOT.parent / "pg18-install-clang" / "bin"))
    parser.add_argument("--pg-data-dir", default=str(REPO_ROOT.parent / "pg18-data-athena-catalog"))
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--user", default=os.environ.get("USER", "postgres"))
    parser.add_argument("--database", default="imdb_full")
    parser.add_argument("--dataset-dir", default=str(DEFAULT_DATASET_DIR))
    parser.add_argument("--workload-dir", default=str(DEFAULT_WORKLOAD_DIR))
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--statement-timeout-ms", type=int, default=30000)
    parser.add_argument("--max-oracle-candidates", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--client-timeout-sec", type=int, default=0,
                        help="Only used in the printed command as a reminder for external wrappers.")
    parser.add_argument("--load", action="store_true", help="Actually load imdb_full into the current PG18.1 server.")
    parser.add_argument("--force-reload", action="store_true", help="Drop and recreate the imdb_full database.")
    parser.add_argument("--run-eval", action="store_true", help="Run evaluate_bound_candidate_oracle.py after load/check.")
    parser.add_argument(
        "--table-limit",
        type=int,
        default=0,
        help="If > 0, only load the first N CSV tables. Useful for smoke tests.",
    )
    return parser.parse_args()


def psql_argv(pg_bin_dir, host, port, user, database):
    return [
        str(Path(pg_bin_dir) / "psql"),
        "-X",
        "-q",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        user,
        "-d",
        database,
    ]


def run(argv, *, input_text=None, check=True):
    return subprocess.run(
        argv,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def resolve_server_endpoint(args):
    if args.host and args.port:
        return args.host, args.port

    pid_path = Path(args.pg_data_dir) / "postmaster.pid"
    if not pid_path.exists():
        raise SystemExit(
            f"Could not find {pid_path}. Pass --host/--port explicitly or start the PG18.1 server first."
        )

    lines = pid_path.read_text().splitlines()
    if len(lines) < 6:
        raise SystemExit(f"Unexpected postmaster.pid format in {pid_path}")

    port = int(lines[3])
    host = lines[4]
    return host, port


def check_assets(args):
    dataset_dir = Path(args.dataset_dir)
    workload_dir = Path(args.workload_dir)
    schema_sql = dataset_dir / "schema_sql" / "postgres.sql"
    schema_json = dataset_dir / "schema.json"

    missing = [
        path for path in [dataset_dir, workload_dir, schema_sql, schema_json]
        if not path.exists()
    ]
    if missing:
        raise SystemExit("Missing required asset(s):\n" + "\n".join(str(path) for path in missing))

    csv_files = sorted(dataset_dir.glob("*.csv"))
    sql_files = sorted(workload_dir.glob("*.sql"))
    if not csv_files:
        raise SystemExit(f"No CSV files found under {dataset_dir}")
    if not sql_files:
        raise SystemExit(f"No SQL workload files found under {workload_dir}")

    return {
        "dataset_dir": dataset_dir,
        "workload_dir": workload_dir,
        "schema_sql": schema_sql,
        "schema_json": schema_json,
        "csv_files": csv_files,
        "sql_files": sql_files,
    }


def check_server(pg_bin_dir, host, port):
    proc = subprocess.run(
        [str(Path(pg_bin_dir) / "pg_isready"), "-h", host, "-p", str(port)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stdout.strip() or proc.stderr.strip() or "pg_isready failed")


def database_exists(pg_bin_dir, host, port, user, database):
    proc = run(
        psql_argv(pg_bin_dir, host, port, user, "postgres") + ["-Atqc", f"select 1 from pg_database where datname = '{database}'"],
        check=True,
    )
    return proc.stdout.strip() == "1"


def recreate_database(pg_bin_dir, host, port, user, database):
    sql = (
        "select pg_terminate_backend(pid) "
        "from pg_stat_activity "
        f"where datname = '{database}' and pid <> pg_backend_pid();\n"
        f"drop database if exists {database};\n"
        f"create database {database};\n"
    )
    run(psql_argv(pg_bin_dir, host, port, user, "postgres"), input_text=sql, check=True)


def apply_schema(pg_bin_dir, host, port, user, database, schema_sql_path):
    run(
        psql_argv(pg_bin_dir, host, port, user, database),
        input_text=schema_sql_path.read_text(),
        check=True,
    )


def copy_table(pg_bin_dir, host, port, user, database, table_name, csv_path, copy_options):
    copy_cmd = f'\\copy "{table_name}" from \'{str(csv_path).replace("\\", "\\\\").replace("\'", "\\\'")}\' {copy_options}\n'
    run(psql_argv(pg_bin_dir, host, port, user, database), input_text=copy_cmd, check=True)


def vacuum_analyze(pg_bin_dir, host, port, user, database):
    run(psql_argv(pg_bin_dir, host, port, user, database), input_text="vacuum analyze;\n", check=True)


def load_real_imdb(args, assets, host, port):
    schema = json.loads(assets["schema_json"].read_text())
    copy_options = schema["db_load_kwargs"]["postgres"]

    if args.force_reload or not database_exists(args.pg_bin_dir, host, port, args.user, args.database):
        recreate_database(args.pg_bin_dir, host, port, args.user, args.database)
    else:
        raise SystemExit(
            f"Database {args.database} already exists. Re-run with --force-reload to overwrite it."
        )

    apply_schema(args.pg_bin_dir, host, port, args.user, args.database, assets["schema_sql"])

    csv_files = assets["csv_files"]
    if args.table_limit > 0:
        csv_files = csv_files[: args.table_limit]

    for csv_path in csv_files:
        table_name = csv_path.stem
        print(f"[load] {table_name}", file=sys.stderr, flush=True)
        copy_table(args.pg_bin_dir, host, port, args.user, args.database, table_name, csv_path, copy_options)

    vacuum_analyze(args.pg_bin_dir, host, port, args.user, args.database)


def eval_command(args, assets, host, port):
    cmd = [
        "python3",
        "scripts/evaluate_bound_candidate_oracle.py",
        "--pg-bin-dir",
        args.pg_bin_dir,
        "--host",
        host,
        "--port",
        str(port),
        "--user",
        args.user,
        "--database",
        args.database,
        "--skip-schema-init",
        "--queries-dir",
        str(assets["workload_dir"]),
        "--output-json",
        args.output_json,
        "--statement-timeout-ms",
        str(args.statement_timeout_ms),
        "--max-oracle-candidates",
        str(args.max_oracle_candidates),
        "--repetitions",
        str(args.repetitions),
    ]
    if args.limit > 0:
        cmd.extend(["--limit", str(args.limit)])
    return cmd


def main():
    args = parse_args()
    assets = check_assets(args)
    host, port = resolve_server_endpoint(args)
    check_server(args.pg_bin_dir, host, port)

    print(f"PG18.1 server: host={host} port={port}")
    print(f"Dataset dir: {assets['dataset_dir']}")
    print(f"CSV tables: {len(assets['csv_files'])}")
    print(f"Workload dir: {assets['workload_dir']}")
    print(f"SQL queries: {len(assets['sql_files'])}")

    if args.load:
        load_real_imdb(args, assets, host, port)

    cmd = eval_command(args, assets, host, port)
    print("\nReal-data oracle command:")
    print(" ".join(cmd))
    if args.client_timeout_sec > 0:
        print(f"# Suggested outer wrapper timeout: {args.client_timeout_sec} seconds")

    if args.run_eval:
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
        raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
