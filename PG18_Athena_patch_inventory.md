# PG16.1 Athena_PG -> PG18.1 Patch Inventory

## Purpose

This document isolates the Athena-specific and Lero-specific changes in the
current PG16.1-based fork and maps them to PostgreSQL 18.1 touchpoints.

Reference baseline:

- base commit: `3edc6580c0` (`Stamp 16.1.`)
- current fork head: `a06410cb3b`

Diff summary:

- `17 files changed, 669 insertions(+), 31 deletions(-)`

## Changed Files

### JOP / Athena Core

- `src/backend/optimizer/plan/planner.c`
- `src/backend/optimizer/path/allpaths.c`
- `src/backend/optimizer/path/joinpath.c`
- `src/backend/optimizer/util/pathnode.c`
- `src/backend/optimizer/jop/jop_extension.c`
- `src/include/optimizer/jop_extension.h`
- `src/backend/utils/misc/guc_tables.c`
- `src/backend/optimizer/Makefile`
- `src/backend/optimizer/jop/Makefile`
- `src/include/optimizer/cost.h`
- `src/backend/optimizer/path/costsize.c`

### Lero Baseline

- `src/backend/lero/lero_extension.c`
- `src/include/lero/lero_extension.h`
- `src/backend/lero/Makefile`
- `src/backend/Makefile`
- `src/backend/optimizer/plan/planner.c`
- `src/backend/optimizer/path/costsize.c`
- `src/backend/utils/misc/guc_tables.c`

### Documentation

- `README` -> deleted
- `README.md` -> added

## Functional Classification

### 1. Planner Entry / Dispatch

#### Current PG16.1 fork

File: `src/backend/optimizer/plan/planner.c`

Changes:

- adds `#include "lero/lero_extension.h"`
- adds `#include "optimizer/jop_extension.h"`
- changes `planner()` so `enable_lero` diverts planning through
  `lero_pgsysml_hook_planner(...)`
- in `standard_planner()`, calls `save_join_order_plans(root, final_rel->pathlist)`
  before selecting `best_path`

Intent:

- Lero hijacks the planning entry point
- Athena dumps the final root candidate set before pruning to one chosen path

PG18.1 mapping:

- `src/backend/optimizer/plan/planner.c`
- `planner()` at lines `290-299`
- `standard_planner()` at lines `303-441`
- `subquery_planner(...)` call at line `435`
- `final_rel` / `best_path` selection at lines `438-441`

Port status:

- carry Athena hook point forward
- do not keep file output
- do not carry Lero in the first PG18 port

### 2. Join Search State Toggle

#### Current PG16.1 fork

File: `src/backend/optimizer/path/allpaths.c`

Changes:

- in `standard_join_search()`, sets `save_join_order_plan_finished = false`
  before join DP starts
- sets `save_join_order_plan_finished = true` after final joinrel is produced

Intent:

- signal whether Athena candidate retention is still inside join exploration
  or has moved into the final post-search stage

PG18.1 mapping:

- `standard_join_search(...)` still exists in
  `src/backend/optimizer/path/allpaths.c`
- same conceptual insertion point is available

Port status:

- carry forward in spirit
- likely replace the flag with planner-private catalog state once the
  in-memory candidate catalog exists

### 3. Root-Level Candidate Capture in Join Construction

#### Current PG16.1 fork

File: `src/backend/optimizer/path/joinpath.c`

Changes:

- `add_paths_to_joinrel(...)` detects whether the current `joinrel` is the
  root relation
- if it thinks the joinrel is root, it temporarily empties `joinrel->pathlist`
  before more paths are added, then appends the newly generated paths back
  into the original list afterwards

Current implementation issue:

- root detection compares `root->all_baserels->words[0]` and
  `joinrelids->words[0]`
- this is not a safe relid-set equality check

Intent:

- weaken root-level pruning so more top-level alternatives survive

PG18.1 mapping:

- `src/backend/optimizer/path/joinpath.c`
- `add_paths_to_joinrel(...)` at lines `124-130`

Port status:

- carry forward concept
- replace root detection with `bms_equal(joinrelids, root->all_baserels)`
- redesign around explicit root-candidate capture, not pathlist swapping

### 4. Path Pruning Weakening

#### Current PG16.1 fork

File: `src/backend/optimizer/util/pathnode.c`

Changes:

- in `add_path(...)`, if `enable_join_order_plans && save_join_order_plan_finished`
  then force `costcmp = COSTS_DIFFERENT`

Intent:

- keep more candidate paths by disabling normal fuzzy cost dominance

Risk:

- this is a coarse global knob
- it is not targeted to root-level candidates only

PG18.1 mapping:

- `src/backend/optimizer/util/pathnode.c`
- `add_path(...)` at lines `464-497`

Port status:

- do not port literally
- replace with explicit candidate catalog logic and blueprint retention

### 5. Join Order Serialization / File Output

#### Current PG16.1 fork

Files:

- `src/backend/optimizer/jop/jop_extension.c`
- `src/include/optimizer/jop_extension.h`

Changes:

- adds `catch_join_order(...)` that recursively converts a `Path *` tree into a
  parenthesized join order string
- adds `save_join_order_plans(...)` that writes candidate strings to
  `/tmp/Athena_join_order_plans.txt`

Current implementation characteristics:

- supports only a subset of path node types
- several node types call `elog(WARNING)` or `elog(ERROR)`
- special-cases `ProjectionPath` under `T_Result`
- serializes alias names from `root->parse->rtable`

Intent:

- externalize root candidate join orders for Athena-side consumption

PG18.1 mapping:

- no direct port as-is
- the replacement belongs in a new Athena module, not under a file dump path

Port status:

- replace with in-memory candidate catalog
- later replace stringification with completed-plan serialization

### 6. GUC / Global State Additions

#### Current PG16.1 fork

Files:

- `src/backend/utils/misc/guc_tables.c`
- `src/backend/optimizer/path/costsize.c`
- `src/include/optimizer/cost.h`

Added state:

- `enable_join_order_plans`
- `save_join_order_plan_finished`
- `enable_lero`
- `lero_swing_factor`
- `lero_subquery_table_num`

Intent:

- expose Athena / Lero toggles through PostgreSQL configuration

PG18.1 mapping:

- `src/backend/utils/misc/guc_tables.c`
- `src/backend/optimizer/path/costsize.c`
- `src/include/optimizer/cost.h`

Port status:

- `enable_join_order_plans` should be kept
- `save_join_order_plan_finished` should probably move to Athena-private state
- Lero GUCs are optional and out of scope for the first port

### 7. Subquery Exposure to Outer Planning

#### Current PG16.1 fork

File:

- `src/backend/optimizer/path/allpaths.c`

Observation:

- Athena_PG does not yet modify `set_subquery_pathlist(...)`
- current behavior is still the PostgreSQL default:
  every `sub_final_rel->pathlist` member becomes a `SubqueryScanPath`

Why this matters:

- this is the correct insertion point for the new PG18 late-bound subquery
  frontier extraction

PG18.1 mapping:

- `src/backend/optimizer/path/allpaths.c`
- `set_subquery_pathlist(...)` at lines `2529-2783`

Port status:

- new work starts here for V1
- this is not a direct port of existing Athena code

### 8. Direct Execution Hook Feasibility

#### Current PG16.1 fork

File:

- `src/backend/optimizer/plan/createplan.c`

Observation:

- `create_subqueryscan_plan(...)` recursively calls
  `create_plan(rel->subroot, best_path->subpath)`

Why this matters:

- direct path selection is feasible because a stored completed `Path *`
  naturally recurses into subquery `subpath`

PG18.1 mapping:

- `src/backend/optimizer/plan/createplan.c`
- `create_subqueryscan_plan(...)` at lines `3695-3743`

Port status:

- keep this mechanism
- no need for `pg_hint_plan`-style reconstruction

### 9. Lero Hooking

#### Current PG16.1 fork

Files:

- `src/backend/lero/lero_extension.c`
- `src/include/lero/lero_extension.h`
- `src/backend/optimizer/path/costsize.c`
- `src/backend/optimizer/plan/planner.c`

Behavior:

- first run `standard_planner(copyObject(parse), ...)`
- record join cardinalities during `set_joinrel_size_estimates(...)`
- rescale selected cardinalities based on join input table count
- rerun `standard_planner(...)`

Port status:

- explicitly exclude from the first PG18 Athena port
- revisit only if a Lero baseline is required later

## PG18.1 Signature Checks

Verified against local PostgreSQL 18.1 source:

- `standard_planner(Query *parse, const char *query_string, int cursorOptions,
  ParamListInfo boundParams)` still has the 4-argument signature
- `subquery_planner(...)` is
  `subquery_planner(PlannerGlobal *glob, Query *parse, PlannerInfo *parent_root,
  bool hasRecursion, double tuple_fraction, SetOperationStmt *setops)`
- `set_subquery_pathlist(...)` still loops over `sub_final_rel->pathlist` and
  wraps each subpath in `create_subqueryscan_path(...)`
- `create_subqueryscan_plan(...)` still recursively plans `best_path->subpath`

## Porting Decision Table

### Carry Forward Directly

- `enable_join_order_plans` GUC
- top-level post-`final_rel` candidate interception in `standard_planner()`
- `standard_join_search()` state entry / exit hook points
- recursive execution path through `create_subqueryscan_plan()`

### Redesign While Porting

- root candidate retention in `add_paths_to_joinrel()`
- pruning weakening in `add_path()`
- join order serialization
- candidate persistence format
- all subquery handling for late-bound assembly

### Exclude From First PG18 Milestone

- Lero hook planner path
- `/tmp/Athena_join_order_plans.txt`
- literal `Path *` mutation or post-hoc cost patching

## Step 2 Exit Criteria

Step 2 can be considered complete when:

- the Athena-specific patch areas are isolated
- each area is mapped to a PG18.1 file/function
- every area is classified as direct port, redesign, or exclude
- known factual mismatches in the main implementation plan are corrected
