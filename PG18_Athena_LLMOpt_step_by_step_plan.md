# PostgreSQL 18 + Athena + LLMOpt Step-by-Step Implementation Plan

## 0. Goal

The target system is:

- PostgreSQL 18 as the experimental DBMS platform
- Athena's order-centric exploration ported into PG18
- no `pg_hint_plan` for plan application
- candidate application done by direct path selection inside the planner
- exploration and post-binding cost evaluation decoupled inside the DBMS
- LLM used only as a reranker over completed Top-K plans

The core idea is:

1. Generate top-level skeletons with Athena-style order-centric exploration.
2. Generate a bounded subquery alternative frontier locally inside the DBMS.
3. After exploration, perform late-bound assembly and post-binding cost recomputation.
4. Keep only a small completed candidate set.
5. Let the LLM choose among completed plans.
6. Execute the chosen `Path *` directly via `create_plan()`.

## 1. Scope Freeze for V1

Do not try to solve everything in the first implementation.

V1 supports:

- non-correlated `FROM` subqueries represented as `SubqueryScan`
- top-level order-centric exploration only
- subquery-local alternative extraction only
- one-shot completed-plan prompting
- direct path selection in the planner

Implementation note:

- do not introduce Athena-style order-centric exploration inside subqueries for V1
- keep subqueries as local alternative frontiers only, to avoid reopening a large top-level x subquery exploration space

V1 explicitly excludes:

- correlated scalar subqueries
- `EXISTS`/`IN` sublinks
- recursive CTEs
- view rewrite corner cases
- broad prompt engineering work

The research claim for V1 should be:

- better completed candidate quality than Bao-style few-plan generation
- lower planning overhead than flat cross-product exploration
- better nested-query applicability than hint-based application pipelines

## 2. Architecture Target

The final architecture should have three phases.

### 2.1 Exploration Phase

Inside PostgreSQL:

- top-level candidate skeletons are extracted during join exploration
- subquery alternatives are extracted locally inside subquery planning
- no full top-level x subquery cross-product is created during dynamic programming

### 2.2 Evaluation Phase

After exploration:

- each top-level skeleton is late-bound with compatible subquery alternatives
- post-binding planner cost is recomputed for each completed candidate
- the best completed plan per skeleton is retained
- a small global Top-K candidate set is built for downstream selection

### 2.3 Selection / Execution Phase

- the completed candidates are serialized for the selector
- the selector returns one candidate index
- the DBMS picks the corresponding stored `Path *`
- `create_plan()` is called directly on that chosen path

## 3. Verified Touchpoints

### 3.1 Current Athena_PG Touchpoints

The current local fork modifies these areas:

- `src/backend/optimizer/plan/planner.c`
- `src/backend/optimizer/path/joinpath.c`
- `src/backend/optimizer/util/pathnode.c`
- `src/backend/optimizer/path/allpaths.c`
- `src/backend/optimizer/plan/createplan.c`

Relevant current behavior:

- `planner()` gets `final_rel` and currently dumps `final_rel->pathlist` before choosing `best_path`
- root candidate capture is injected into `add_paths_to_joinrel()`
- path pruning is weakened in `add_path()`
- subquery paths are exposed to the outer query via `set_subquery_pathlist()`
- `create_subqueryscan_plan()` recursively plans the selected `subpath`

### 3.2 PostgreSQL 18 Touchpoints

PG18 still exposes the same conceptual control points:

- `standard_planner(...)`
- `subquery_planner(...)`
- `standard_join_search(...)`
- `set_subquery_pathlist(...)`
- `add_paths_to_joinrel(...)`
- `add_path(...)`

PG18 source references:

- https://doxygen.postgresql.org/planner_8c_source.html
- https://doxygen.postgresql.org/allpaths_8c_source.html
- https://doxygen.postgresql.org/joinpath_8c_source.html
- https://doxygen.postgresql.org/pathnode_8c_source.html

Important PG18 differences from the current PG16.1-based fork:

- `standard_planner()` keeps the 4-argument signature in PG18.1
- `subquery_planner()` signature differs from PG16.1
- planner hook signatures must be checked carefully during porting
- path and planner data structure lifetimes must be revalidated on PG18

## 4. Step-by-Step Plan

### Step 1. Build a Clean PG18 Baseline

Tasks:

- build vanilla PostgreSQL 18
- load target benchmark datasets
- record baseline planning time and execution time
- verify `EXPLAIN` output and planner debug workflow

Deliverable:

- a reproducible PG18 baseline environment

Done when:

- PG18 can run the target workload end-to-end
- baseline planning/execution metrics are saved

### Step 2. Diff Athena_PG Against PG16.1 and Map to PG18

Tasks:

- isolate the Athena-specific patch set from the current repo
- classify changes into:
  - top-level exploration logic
  - path retention logic
  - GUC additions
  - file output / instrumentation
  - Lero baseline
- map each changed area to the corresponding PG18 source location

Deliverable:

- a patch inventory document or checklist

Done when:

- every Athena-specific change has a target location in PG18

### Step 3. Port Only the Minimal Athena Core First

Tasks:

- port GUCs needed for order-centric exploration
- port top-level candidate retention logic
- port planner entry-point logic
- remove `/tmp/Athena_join_order_plans.txt` output
- replace `words[0]` relid comparison with `bms_equal()`

Files likely touched:

- `src/backend/optimizer/plan/planner.c`
- `src/backend/optimizer/path/joinpath.c`
- `src/backend/optimizer/util/pathnode.c`
- `src/backend/utils/misc/guc_tables.c`
- `src/include/...` headers for any Athena-specific declarations

Deliverable:

- PG18 build with root-level Athena exploration enabled

Done when:

- PG18 builds
- top-level candidate collection runs without crashing
- a debug print or in-memory dump shows multiple root-level candidates

### Step 4. Introduce an In-Memory Candidate Catalog

Tasks:

- create a planner-private candidate catalog instead of file output
- define explicit structs for:
  - `TopSkeletonCandidate`
  - `SubqueryAlternative`
  - `BoundCandidate`
  - `PropertySignature`
- define a dedicated temporary `MemoryContext` strategy for late-bound instantiation and evaluation
- assign stable IDs to candidates and subquery slots

Suggested new module split:

- `src/backend/optimizer/athena/candidate_catalog.c`
- `src/backend/optimizer/athena/latebind_eval.c`
- `src/backend/optimizer/athena/serialize.c`
- matching headers under `src/include/optimizer/`

Deliverable:

- reusable in-memory representation for later phases

Done when:

- the planner can hold candidate metadata without writing any files
- the catalog design clearly separates long-lived planner state from throwaway instantiation state

### Step 5. Implement Top-Level Skeleton Extraction

Tasks:

- keep Athena's order-centric search at the top level
- convert the retained root candidates into blueprint-style skeleton objects
- mark subquery leaves as placeholder slots rather than immediately expanding them
- record:
  - root join tree
  - join operator choices
  - child blueprint references
  - relevant restrict clauses / join metadata needed to rebuild paths
  - high-level cost metadata for ranking and debugging
  - slot IDs for any subqueries referenced in the skeleton

Important design decision:

- the skeleton must be a reconstruction blueprint, not a mutable `Path *` tree
- the blueprint must carry enough information to re-instantiate a valid `Path *` tree bottom-up using PostgreSQL path constructors
- but it must not materialize the full top-level x subquery cross-product

Deliverable:

- a stable top-level skeleton catalog after `final_rel` is formed

Done when:

- the system can print a list of root skeletons with their slot layout

### Step 6. Implement Subquery-Local Alternative Extraction

Tasks:

- hook into subquery planning at `set_subquery_pathlist()`
- extract a bounded subquery-local frontier
- record per alternative:
  - root join method family
  - `pathkeys`
  - `required_outer`
  - parallel / partial properties
  - startup and total cost
  - rows and width
- keep only local alternatives needed for late binding

V1 policy:

- start with non-correlated `FROM` subqueries only
- use capability-based frontier pruning, not full exposure of all subpaths

Deliverable:

- subquery alternative catalogs keyed by subquery slot ID

Done when:

- each relevant subquery has an explicit alternative list in memory

### Step 7. Implement Late-Bound Assembly and Blueprint Instantiation

This is the core new functionality.

Tasks:

- after exploration finishes, iterate over top-level skeletons
- for each skeleton, bind compatible subquery alternatives
- instantiate completed candidate paths bottom-up from the blueprint using PostgreSQL path creation routines
- do not mutate existing `Path *` trees in place
- let PostgreSQL recompute cost as part of path construction rather than trying to patch old costs
- enforce required physical properties during instantiation
- retain the best completed plan per skeleton
- apply structural diversity filtering so the final global Top-K is not homogeneous
- build the final candidate set that will be seen by the selector

Important rule:

- this phase happens after dynamic programming exploration
- it must not call back into full join-order search
- it may rebuild fixed path trees and let PostgreSQL cost them, but it must not re-open combinatorial search

Safety requirements:

- if a top-level operator requires sorted input, compare required `pathkeys` with the chosen subquery alternative
- when the alternative does not satisfy the required `pathkeys`, insert an explicit sort enforcer before constructing the parent path
- reject combinations that violate non-repairable interface constraints such as incompatible parameterization or parallel assumptions

Memory requirements:

- completed candidates that survive selection may remain in the planner context
- throwaway instantiated candidates should be built in a temporary `MemoryContext` when the candidate population becomes large
- if temporary contexts are not yet implemented, Step 6 must keep subquery alternative frontiers aggressively bounded

V1 simplification:

- one best completed plan per skeleton is enough

Deliverable:

- completed `BoundCandidate` objects with valid instantiated `Path *` trees and planner-computed cost

Done when:

- for each top-level skeleton, the system can show one fully-bound best completion
- the final candidate set contains both low-cost and structurally diverse completed plans

### Step 8. Implement Direct Path Selection

Tasks:

- after candidate selection, map `best_candidate_idx` to a stored `BoundCandidate`
- ensure the selected candidate owns a valid `Path *`
- pass that chosen path directly to `create_plan()`
- bypass hint reconstruction entirely

Important invariant:

- every completed candidate kept for execution must be representable as a valid `Path *`

Deliverable:

- direct execution of a chosen completed candidate inside the planner

Done when:

- the planner can execute a non-default chosen candidate without `pg_hint_plan`

### Step 9. Build a Non-LLM Oracle Evaluation First

Do not add the LLM yet.

Tasks:

- run the system and collect completed candidates
- identify the actual runtime best candidate per query
- compare:
  - default PG18 chosen plan
  - planner-cost minimum completed candidate
  - runtime oracle completed candidate

Key question:

- does the completed candidate set contain meaningfully better plans than the baseline?

Deliverable:

- oracle candidate-quality results
- implementation artifacts:
  - `scripts/evaluate_bound_candidate_oracle.py`
  - valid-query / oracle-eligible subset extraction for nested-query workloads
  - repeated-measurement median runtime collection via `--repetitions`
  - synthetic V1 nested-query generation and filtering via:
    - `scripts/generate_job_v1_nested_queries.py`
    - `scripts/filter_subquery_scan_queries.py`

Done when:

- you can show that the new generator is worth feeding into a selector

### Step 10. Add Candidate Serialization for Selector Input

Tasks:

- serialize completed candidates to a canonical prompt-friendly format
- include:
  - fully-bound recomputed total cost
  - startup cost
  - estimated rows
  - full plan tree
  - scan methods
  - join methods
  - interesting physical properties
- keep deterministic candidate ordering

Deliverable:

- `candidate_plans` JSON records ready for selector training/inference
- implementation artifact:
  - `scripts/build_candidate_plan_dataset.py`
  - backend-native plan summaries emitted from
    `src/backend/optimizer/athena/serialize.c`

Done when:

- the same query always produces stable candidate IDs and stable serialized order

### Step 11. Adapt LLMOpt to the New Candidate Format

Tasks:

- replace `shuffled_hints` with `candidate_plans`
- replace `best_hints` label generation with `best_candidate_idx`
- keep the selector as an index-prediction problem
- adjust prompt generation code and inference output parsing

Likely local touchpoints:

- `LLMOpt/LLM_training/utils/load_sft_dataset.py`
- `LLMOpt/LLM_training/inference_vllm.py`
- training / inference scripts under `LLMOpt/scripts/`

Deliverable:

- a selector that consumes completed candidates instead of hint strings
- implementation artifacts:
  - `LLMOpt/LLM_training/utils/plan_prompt_utils.py`
  - `LLMOpt/LLM_training/utils/load_sft_dataset.py`
  - `LLMOpt/LLM_training/inference_vllm.py`

Done when:

- training and inference run on `candidate_plans`
- the model returns `best_candidate_idx`

### Step 12. Add a Cheap Non-LLM Selector Baseline

Before evaluating the LLM, add cheap baselines.

Tasks:

- planner-cost minimum baseline
- heuristic reranker baseline
- optional lightweight classifier or small tree model baseline

Reason:

- if the LLM does not beat these, the selector contribution is weak

Deliverable:

- competitive non-LLM reranking baselines
- implementation artifact:
  - `scripts/evaluate_candidate_plan_baselines.py`

Done when:

- the LLM is compared against more than just default PG cost

### Step 13. Integrate the LLM Selector

Tasks:

- train or fine-tune on the new candidate format
- run inference on completed candidates only
- compare against the non-LLM baselines

Important interpretation:

- the LLM is no longer a plan generator
- the LLM is a robust reranker over completed candidate plans

Deliverable:

- end-to-end DBMS + selector pipeline

Done when:

- the system can choose a completed candidate index and execute it inside PG18

### Step 14. Run the Full Experimental Ladder

The evaluation should be staged in this order:

1. PG18 vanilla
2. PG18 + Athena root exploration only
3. PG18 + Athena + late-bound completed candidate generation
4. Step 3 + planner-cost selector
5. Step 3 + cheap non-LLM selector
6. Step 3 + LLM selector

Metrics:

- planning time
- execution time
- candidate count
- oracle gap
- selector accuracy
- nested-query coverage

Deliverable:

- results that separate generator quality from selector quality

Done when:

- you can explain where the final gain comes from

## 5. Recommended File-Level Work Split

### PostgreSQL 18 Fork

Existing files likely touched:

- planner entry:
  - `src/backend/optimizer/plan/planner.c`
- top-level exploration:
  - `src/backend/optimizer/path/joinpath.c`
  - `src/backend/optimizer/util/pathnode.c`
- subquery integration:
  - `src/backend/optimizer/path/allpaths.c`
- final plan creation:
  - `src/backend/optimizer/plan/createplan.c`
- configuration:
  - `src/backend/utils/misc/guc_tables.c`

New files recommended:

- `src/backend/optimizer/athena/candidate_catalog.c`
- `src/backend/optimizer/athena/latebind_eval.c`
- `src/backend/optimizer/athena/serialize.c`
- `src/include/optimizer/athena_candidate.h`
- `src/include/optimizer/athena_latebind.h`

### LLMOpt Side

Existing files likely touched:

- `LLMOpt/LLM_training/utils/load_sft_dataset.py`
- `LLMOpt/LLM_training/inference_vllm.py`
- `LLMOpt/scripts/train_sel.bash`
- `LLMOpt/scripts/infer_sel.bash`

Recommended new artifacts:

- a candidate-plan data generator script
- a runtime label collector
- a new prompt template for completed plan comparison
- a cheap baseline evaluator for `candidate_plans`
- an oracle-evaluation script for default PG18 vs planner-cost minimum vs runtime oracle completed candidates

## 6. Decision Gates

### Gate A. After Step 5

Question:

- do the top-level skeletons show better candidate diversity than the baseline?

If no:

- stop and fix Athena port quality first

### Gate B. After Step 7

Question:

- does late-bound completed candidate generation have acceptable planning overhead?

If no:

- shrink subquery frontiers
- optimize post-binding re-costing
- reduce candidate retention policy

### Gate C. After Step 9

Question:

- does the oracle best completed candidate beat the default plan often enough?

If no:

- the selector is not the main problem; improve candidate generation instead

### Gate D. After Step 13

Question:

- does the LLM beat cheap reranking baselines?

If no:

- the generator may still be useful, but the LLM story weakens

## 7. Recommended Execution Order

Do the work in this exact order:

1. PG18 baseline
2. Athena minimal port
3. in-memory candidate catalog
4. top-level skeleton extraction
5. subquery-local frontier extraction
6. late-bound completed candidate generation
7. direct path selection
8. oracle experiments
9. candidate serialization
10. cheap selector baselines
11. LLMOpt integration
12. final experiments

This order minimizes wasted work because it verifies candidate quality before investing in prompt design or model training.

## 8. Short Version

If the project needs a single operational sentence, it is:

Port Athena's top-level order-centric exploration to PostgreSQL 18, keep subquery alternatives local, assemble only a bounded set of completed plans after exploration, execute the chosen completed plan by direct path selection, and use LLMOpt only as a reranker over those completed plans.

## 9. Current Status

Implemented and checked:

- Step 1 through Step 8 on PostgreSQL 18.1
- Step 9 oracle evaluation infrastructure, including repeated median measurement and backend cleanup
- Step 10 dataset generation from `candidate_plans`, with backend-native bound-candidate summaries
- Step 11 prompt/data-path adaptation in `LLMOpt`
- Step 12 cheap selector baselines
- a protected default-baseline candidate is now injected into the bound-candidate set and preserved by the oracle / dataset builders
- `athena_max_distinct_bindings_per_skeleton` now allows more than one binding-distinct completion per skeleton; this directly targets the previous collapse to a single subquery-binding profile
- `make check-world` passes on the PG18.1 fork after the Athena changes
- synthetic nested-query evaluation now runs on both the 50-query and 195-query generated JOB V1 workloads
- the 195-query synthetic run currently yields:
  - `oracle_eligible_count = 195`
  - `oracle_better_than_default_count = 189`
  - `planner_min_equal_oracle_count = 119`
- remaining `Agg` / `Opaque` hotspots have been reduced substantially by adding blueprint support for:
  - `Agg`
  - `UpperUnique`
  - `Limit`
  - `IncrementalSort`
- loss analysis on `tmp/oracle_generated_job_v1_195q_real_imdb_v4.json` shows that the 39 real-data non-improving queries are concentrated in a few `title_*` families and, before the new binding-retention change, every one of them collapsed to exactly one non-default binding profile
- on a targeted 4-query loss subset, increasing `athena_max_distinct_bindings_per_skeleton` from `1` to `2` flipped 3 queries from default-wins to oracle-wins while increasing per-query bound-candidate counts from roughly `14-18` to `27-35`
- however, a partial real-data rerun on the first 71 queries shows that naive `binding=2` is not uniformly better: it flips 7 old losses to wins, but also flips 10 old wins to losses, indicating that extra binding variants can crowd out other good candidates under the current cost-top-`K` oracle truncation
- the oracle / dataset builders now use a binding-aware top-`K` truncation policy: protected default, then cheapest-per-binding with skeleton spreading, then cheapest-per-skeleton, then cost-fill
- on a 17-query real-data probe subset that mixed the 7 naive-`binding=2` wins and 10 naive-`binding=2` regressions, binding-aware truncation improved the result to `12/17` oracle wins versus `10/17` for the original `binding=1` and `7/17` for naive `binding=2`
- a full 195-query real-data rerun with `athena_max_distinct_bindings_per_skeleton = 2` and binding-aware top-`K` truncation now completes successfully:
  - oracle report: `tmp/oracle_generated_job_v1_195q_real_imdb_bind2_aware_v1.json`
  - `oracle_eligible_count = 175`
  - `oracle_better_than_default_count = 148`
  - `default_not_worse_than_oracle_count = 27`
  - `planner_min_equal_oracle_count = 71`
- compared with the previous real-data `v4` run, the new policy improves the headline oracle win count from `137/176` to `148/175`
- the downstream dataset and cheap baselines have been regenerated for the new policy:
  - dataset: `tmp/candidate_plans_generated_job_v1_195q_real_imdb_bind2_aware_v1.json`
  - baselines: `tmp/candidate_plan_baselines_generated_job_v1_195q_real_imdb_bind2_aware_v1.json`
  - `planner_cost_min_accuracy = 0.4057`
  - `structure_first_accuracy = 0.3543`
- a restricted dual-representation path for simple `FROM`-subqueries is now implemented as a practical two-mode collection strategy:
  - new GUC: `athena_disable_simple_from_subquery_pullup`
  - planner-prep change in `prepjointree.c`: eligible simple `FROM`-subqueries can now be kept unflattened instead of always being pulled up
  - evaluator change: `evaluate_bound_candidate_oracle.py --athena-dual-simple-from-subqueries` collects both `flattened` and `no_pullup` candidates and merges them into one candidate set with `representation_mode` and `local_idx`
  - dataset builder preserves the same representation metadata
  - smoke artifact: `tmp/oracle_dual_smoke_v1.json`
  - smoke result on a simple IMDB `FROM`-subquery shows distinct per-mode frontiers:
    - `root_candidate_count_by_mode = {'flattened': 3, 'no_pullup': 2}`
    - `bound_candidate_count_by_mode = {'flattened': 4, 'no_pullup': 3}`
    - merged candidates include both `Merge Join(t, movie_keyword)` and `Merge Join(t, mk)` / `Nested Loop(mk, t)` style roots
- a full 195-query real-data rerun with the new dual-representation mode has also been completed:
  - oracle report: `tmp/oracle_generated_job_v1_195q_real_imdb_dualrepr_v1.json`
  - dataset: `tmp/candidate_plans_generated_job_v1_195q_real_imdb_dualrepr_v1.json`
  - baselines: `tmp/candidate_plan_baselines_generated_job_v1_195q_real_imdb_dualrepr_v1.json`
  - `oracle_eligible_count = 157`
  - `oracle_better_than_default_count = 129`
  - `default_not_worse_than_oracle_count = 28`
  - `planner_min_equal_oracle_count = 35`
  - `planner_cost_min_accuracy = 0.2229`
  - `structure_first_accuracy = 0.2420`
- compared with the current best single-representation binding-aware run (`bind2_aware_v1`), the first dual-representation version is not yet a win:
  - single-representation binding-aware: `148/175`
  - dual representation v1: `129/157`
  - among queries comparable in both runs, dual representation flips `12` old losses to wins but also flips `15` old wins to losses
  - the main issue is candidate crowding: the merged `flattened` and `no_pullup` frontiers roughly double per-query candidate counts, but the downstream oracle / prompt budget is still fixed
- the oracle / dataset selection policy has now been updated again for the single-representation path:
  - exact PostgreSQL baseline is materialized from an explicit `enable_join_order_plans = off` run and injected as a synthetic protected candidate
  - candidate budgeting now treats `K` as the total budget and uses:
    - exact default baseline
    - cheapest non-default
    - best sorted / merge-friendly non-default
    - remaining slots filled by binding-aware cost selection
  - this has been wired into both `evaluate_bound_candidate_oracle.py` and `build_candidate_plan_dataset.py`
- a 39-query real-data loss-focused subset run with `K = 5` confirms the intended effect:
  - oracle report: `tmp/oracle_default_loss_subset_bind2_k5_exactdefault_v1.json`
  - `oracle_eligible_count = 36`
  - `oracle_better_than_default_count = 22`
  - `default_not_worse_than_oracle_count = 14`
  - crucially, all remaining `14` losses are now exact-default ties rather than strict regressions, i.e. `oracle == exact_default` for all of them
- a full 195-query real-data rerun with the same `K = 5` exact-default policy now completes successfully:
  - oracle report: `tmp/oracle_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json`
  - dataset: `tmp/candidate_plans_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json`
  - baselines: `tmp/candidate_plan_baselines_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json`
  - `default_runtime_success_count = 177`
  - `oracle_eligible_count = 177`
  - `oracle_better_than_default_count = 146`
  - `default_not_worse_than_oracle_count = 31`
  - `planner_min_equal_oracle_count = 65`
  - `planner_cost_min_accuracy = 0.3672`
  - `structure_first_accuracy = 0.2825`
  - `oracle_avg_runtime_ms = 5352.58`
  - importantly, all `31` non-improving cases in this run are exact-default ties; strict regressions are now `0`

Current bottlenecks:

- real-data IMDB evaluation is now in place, but some queries still miss runtime comparison because `statement_timeout = 30000` cuts them off
- flat JOB analysis found a separate planner-side bottleneck: `24a`-family queries hit the GEQO path (`12` relations, `geqo_threshold = 12`), and `enable_join_order_plans = on` can time out during GEQO initial-pool evaluation even for `EXPLAIN (FORMAT JSON)` with no execution
- the new phase timing shows that `24a.sql` does **not** reach root-finalize, late-bind, or serialization before timing out; with `geqo = off`, the same Athena-on planner path finishes in about `0.058s`, so the pathological case is GEQO-specific rather than a general late-bind cost
- Step 13 is code-ready, but the LLM selector has not yet been trained and compared on the new `candidate_plans` datasets
- late-bind is now side-catalog-driven at the root, but unsupported path kinds can still fall back to node-local `template_path`
- exact-default baseline injection is now implemented in the evaluator / dataset builders, but the same exact-baseline notion is not yet materialized inside the PostgreSQL-side in-memory candidate catalog itself
- the new dual-representation path currently merges `flattened` and `no_pullup` candidates outside a single planner invocation; a future step is to integrate the same idea deeper into the in-DB candidate catalog if single-run dual planning becomes necessary
- more importantly, dual representation now needs a better merged-candidate budget policy; `flattened` and `no_pullup` modes are currently merged with a fixed downstream top-`K`, which causes the new representation mode to crowd out previously strong single-representation candidates

## 10. Real-Data Benchmark Path

Local assets already available:

- IMDB real-data CSVs under `/Users/an/Desktop/bachelor/4thyear/2nd_semester/thesis/zero-shot/cross_db_benchmark/datasets/imdb`
- generated nested-query workload under `/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/generated_job_v1_subqueryscan_queries`
- PostgreSQL 18.1 install under `/Users/an/Desktop/bachelor/4thyear/2nd_semester/pg18-install-clang`

Recommended real-data path:

1. Load the local IMDB CSVs into a dedicated `imdb_full` database on the PG18.1 Athena fork.
2. Run `evaluate_bound_candidate_oracle.py` with `--skip-schema-init` against that existing populated database.
3. Rebuild `candidate_plans` and cheap baselines from the real-data oracle report.
4. Only after that, move on to LLM training / inference.

Helper entrypoint:

- `scripts/real_imdb_benchmark.py`

What it does:

- detects the current PG18.1 socket / port from `postmaster.pid`
- verifies local IMDB CSV and workload assets
- can load `imdb_full` using `psql` / `\\copy` only, without requiring `psycopg2`
- prints or runs the real-data Step 9 oracle command against the populated database

Immediate next experiment:

- load `imdb_full`
- run the 195-query generated nested workload against real data
- compare the resulting real-data oracle summary against the current schema-only synthetic summary

Smoke validation already completed:

- `scripts/real_imdb_benchmark.py --database imdb_full_smoke --load --force-reload --table-limit 1`
- this successfully created a temporary database and loaded `aka_name` via `\\copy`
- `scripts/real_imdb_benchmark.py --database imdb_full --load --force-reload`
- this successfully loaded the full local IMDB dataset into the PG18.1 Athena fork
- `scripts/real_imdb_benchmark.py --database imdb_full --run-eval --limit 5`
- this real-data smoke run completed with:
  - `query_count = 5`
  - `oracle_eligible_count = 5`
  - `oracle_better_than_default_count = 4`

Full real-data run now completed:

- output: `tmp/oracle_generated_job_v1_195q_real_imdb_v4.json`
- summary:
  - `query_count = 195`
  - `default_runtime_success_count = 176`
  - `bound_collection_success_count = 176`
  - `oracle_eligible_count = 176`
  - `oracle_better_than_default_count = 137`
  - `default_not_worse_than_oracle_count = 39`
  - `planner_min_equal_oracle_count = 84`

Real-data downstream artifacts:

- `tmp/candidate_plans_generated_job_v1_195q_real_imdb_v4.json`
  - `record_count = 176`
- `tmp/candidate_plan_baselines_generated_job_v1_195q_real_imdb_v4.json`
  - `planner_cost_min_accuracy = 0.4773`
  - `structure_first_accuracy = 0.3807`
