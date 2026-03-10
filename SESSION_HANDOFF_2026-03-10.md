# Session Handoff (2026-03-10)

## 1. What This Project Is

This repo now contains a PostgreSQL 18.1 based experimental platform that combines:

- Athena-style top-level order-centric candidate generation
- subquery-local alternative extraction for non-correlated `FROM` subqueries
- late-bound completed-plan generation inside PostgreSQL
- direct path selection without `pg_hint_plan`
- `candidate_plans` dataset generation for LLM-based reranking

The current non-LLM system is largely working end-to-end.

## 2. Current Best Configs

### Nested real-IMDB workload

Best current nested-query setting:

- single representation
- `athena_max_distinct_bindings_per_skeleton = 2`
- `K = 5`
- exact default candidate protected
- cheapest non-default protected
- best sorted / merge-friendly non-default protected
- remaining 2 slots filled by binding-aware cost fill

Main artifact:

- [oracle_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/oracle_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json)

Summary:

- `query_count = 195`
- `default_runtime_success_count = 177`
- `bound_collection_success_count = 177`
- `oracle_eligible_count = 177`
- `oracle_better_than_default_count = 146`
- `default_not_worse_than_oracle_count = 31`
- `planner_min_equal_oracle_count = 65`

Important interpretation:

- the 31 non-improving cases are exact-default ties
- strict regression count is effectively `0`

Derived dataset / baselines:

- [candidate_plans_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/candidate_plans_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json)
- [candidate_plan_baselines_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/candidate_plan_baselines_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json)

Cheap baseline summary:

- `planner_cost_min_accuracy = 0.3672`
- `structure_first_accuracy = 0.2825`
- `oracle_avg_runtime_ms = 5352.58`
- `planner_cost_min_avg_runtime_ms = 6392.85`

### Flat JOB on real IMDB

Best current flat JOB result uses `geqo=off`.

Main artifact:

- [oracle_job_flat_real_imdb_k5_exactdefault_geqo_off_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/oracle_job_flat_real_imdb_k5_exactdefault_geqo_off_v1.json)

Summary:

- `query_count = 113`
- `default_runtime_success_count = 110`
- `bound_collection_success_count = 110`
- `oracle_eligible_count = 110`
- `oracle_better_than_default_count = 101`
- `default_not_worse_than_oracle_count = 9`
- `planner_min_equal_oracle_count = 21`

Interpretation:

- `101` strict wins
- `9` ties
- `0` strict losses

Derived dataset / baselines:

- [candidate_plans_job_flat_real_imdb_k5_exactdefault_geqo_off_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/candidate_plans_job_flat_real_imdb_k5_exactdefault_geqo_off_v1.json)
- [candidate_plan_baselines_job_flat_real_imdb_k5_exactdefault_geqo_off_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/candidate_plan_baselines_job_flat_real_imdb_k5_exactdefault_geqo_off_v1.json)

Cheap baseline summary:

- `planner_cost_min_accuracy = 0.1818`
- `structure_first_accuracy = 0.0545`
- `oracle_avg_runtime_ms = 987.17`
- `planner_cost_min_avg_runtime_ms = 1263.92`

## 3. Key Conclusions So Far

### Candidate generator quality is strong

- Nested real-IMDB: candidate set contains a better-than-default plan for `146 / 177` comparable queries.
- Flat JOB with `geqo=off`: candidate set contains a better-than-default plan for `101 / 110` comparable queries.

### Exact default containment matters

The system now explicitly injects the exact PostgreSQL default plan into the candidate set used for oracle evaluation and dataset generation. This avoids false regressions caused by protecting an Athena-enabled `best_path` that was not identical to the true `enable_join_order_plans=off` baseline.

### Cheap selectors are still weak

Even after improving candidate quality, cheap selectors do not close the gap to oracle. This is the main reason the LLM stage is worth pursuing.

### GEQO is a real bottleneck for Athena-on flat JOB

For some flat JOB queries around `24a.sql` to `33c.sql`, `enable_join_order_plans=on` timed out in planner-only `EXPLAIN` when GEQO was active, but finished quickly with `geqo=off`.

This is not caused by late-bind or serialization. It is caused by the GEQO evaluation loop repeatedly rebuilding trees and triggering Athena candidate capture.

## 4. Why GEQO Hurts Here

Observed behavior:

- `enable_join_order_plans=off` + planner-only `EXPLAIN`: very fast
- `enable_join_order_plans=on` + planner-only `EXPLAIN`: timeout for some flat JOB queries
- `enable_join_order_plans=on` + `geqo=off`: finishes in tens of milliseconds

Interpretation:

- Athena root capture itself is cheap
- late-bind and serialization are not the dominant cost on these queries
- the dominant cost is GEQO repeatedly evaluating individuals

Important implication:

- for flat JOB style experiments, using `geqo=off` or raising `geqo_threshold` is a highly relevant control setting
- it is plausible prior learned-optimizer work implicitly avoided GEQO, but this has not been proven from released code/papers

## 5. Dual Representation Status

Restricted dual representation for simple `FROM` subqueries was implemented experimentally, but naive merge hurt performance.

Main artifact:

- [oracle_generated_job_v1_195q_real_imdb_dualrepr_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/oracle_generated_job_v1_195q_real_imdb_dualrepr_v1.json)

Summary:

- `query_count = 195`
- `default_runtime_success_count = 157`
- `oracle_eligible_count = 157`
- `oracle_better_than_default_count = 129`

Current interpretation:

- dual representation itself is not necessarily bad
- naive merge plus fixed top-K caused candidate crowding
- current best configuration is still the single-representation pipeline

Recommendation:

- keep dual representation paused unless representation-aware budgeting becomes a research focus

## 6. Main Code Locations

### PostgreSQL fork

- planner entry:
  - [planner.c](/Users/an/Desktop/bachelor/4thyear/2nd_semester/pg18-src/postgresql-18.1/src/backend/optimizer/plan/planner.c)
- top-level root capture:
  - [joinpath.c](/Users/an/Desktop/bachelor/4thyear/2nd_semester/pg18-src/postgresql-18.1/src/backend/optimizer/path/joinpath.c)
- subquery path integration:
  - [allpaths.c](/Users/an/Desktop/bachelor/4thyear/2nd_semester/pg18-src/postgresql-18.1/src/backend/optimizer/path/allpaths.c)
- Athena candidate catalog:
  - [candidate_catalog.c](/Users/an/Desktop/bachelor/4thyear/2nd_semester/pg18-src/postgresql-18.1/src/backend/optimizer/athena/candidate_catalog.c)
- late binding and candidate instantiation:
  - [latebind_eval.c](/Users/an/Desktop/bachelor/4thyear/2nd_semester/pg18-src/postgresql-18.1/src/backend/optimizer/athena/latebind_eval.c)
- candidate serialization:
  - [serialize.c](/Users/an/Desktop/bachelor/4thyear/2nd_semester/pg18-src/postgresql-18.1/src/backend/optimizer/athena/serialize.c)
- Athena structs:
  - [athena_candidate.h](/Users/an/Desktop/bachelor/4thyear/2nd_semester/pg18-src/postgresql-18.1/src/include/optimizer/athena_candidate.h)
  - [athena_latebind.h](/Users/an/Desktop/bachelor/4thyear/2nd_semester/pg18-src/postgresql-18.1/src/include/optimizer/athena_latebind.h)

### Evaluation scripts

- oracle evaluation:
  - [evaluate_bound_candidate_oracle.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/scripts/evaluate_bound_candidate_oracle.py)
- dataset building:
  - [build_candidate_plan_dataset.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/scripts/build_candidate_plan_dataset.py)
- cheap baselines:
  - [evaluate_candidate_plan_baselines.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/scripts/evaluate_candidate_plan_baselines.py)
- loss analysis:
  - [analyze_default_loss_characteristics.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/scripts/analyze_default_loss_characteristics.py)
- real IMDB helper:
  - [real_imdb_benchmark.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/scripts/real_imdb_benchmark.py)

### LLMOpt side

- vLLM inference:
  - [inference_vllm.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/LLMOpt/LLM_training/inference_vllm.py)
- dataset loading:
  - [load_sft_dataset.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/LLMOpt/LLM_training/utils/load_sft_dataset.py)
- prompt utilities:
  - [plan_prompt_utils.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/LLMOpt/LLM_training/utils/plan_prompt_utils.py)
- training:
  - [train.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/LLMOpt/LLM_training/train.py)
- scripts:
  - [train_sel.bash](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/LLMOpt/scripts/train_sel.bash)
  - [infer_sel.bash](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/LLMOpt/scripts/infer_sel.bash)

## 7. Current Evaluation Policy

Current top-K policy for oracle and dataset creation:

- total budget `K = 5`
- exact default baseline protected
- cheapest non-default protected
- best sorted / merge-friendly non-default protected
- remaining 2 slots filled by binding-aware cost fill

This policy was better than earlier:

- naive top-3 + default
- binding=2 without binding-aware fill
- protected default using Athena-enabled best path instead of exact default

## 8. LLM Step Status

The LLM side is code-ready but not yet fully run on the latest datasets.

Current local support already exists for:

- `candidate_plans`
- `best_candidate_idx`
- prompt generation for candidate comparison

But the actual fine-tuning / inference experiments against the latest datasets have not yet been run.

### Recommended model order

1. `meta-llama/Meta-Llama-3.1-8B-Instruct`
2. `Qwen/Qwen3-8B`
3. `mistralai/Ministral-8B-Instruct-2410`

Why:

- Llama 3.1 8B matches the prior paper and is closest to current local code assumptions.
- Qwen3 is a good updated same-class comparison, but likely requires dependency bumps.
- Ministral is a good optional extra, but is the riskiest integration.

### Important caveat for Qwen3 / Ministral

The current LLMOpt environment is old:

- `transformers 4.44.2`
- `vllm==0.6.3.post1`

This is likely fine for Llama 3.1 but not ideal for Qwen3. Qwen3 model cards indicate newer `transformers` / `vllm` are preferable.

Also, current code has model-family-specific assumptions:

- [load_sft_dataset.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/LLMOpt/LLM_training/utils/load_sft_dataset.py) hard-codes Llama assistant start token IDs
- [inference_vllm.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/LLMOpt/LLM_training/inference_vllm.py) hard-codes stop tokens per family, but currently knows Qwen2.5 rather than Qwen3

## 9. Important Reading on LLMOpt Paper Setup

Do not over-trust the reported paper numbers without reproducing them locally.

Why:

- train/test are template-generated and likely close in distribution
- warm cache is used
- best epoch is selected using validation
- nested queries are excluded due to `pg_hint_plan` limitations
- JOB/JOB-EXT test set sizes are not clearly stated in the paper text, though local repo data includes test splits

Local data sizes in `LLMOpt/data`:

- JOB: train `8592`, val `100`, test `113`
- JOB-EXT: train `9722`, val `100`, test `24`
- Stack: train `4850`, val `100`, test `76`

## 10. Recommended Next Steps

### Highest priority

Run Step 13 with the latest datasets:

- nested:
  - [candidate_plans_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/candidate_plans_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json)
- flat JOB:
  - [candidate_plans_job_flat_real_imdb_k5_exactdefault_geqo_off_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/candidate_plans_job_flat_real_imdb_k5_exactdefault_geqo_off_v1.json)

Model order:

1. Llama 3.1 8B
2. Qwen3 8B
3. Ministral 8B if time remains

### Before Qwen3

Probably needed:

- update `transformers`
- update `vllm`
- make assistant boundary detection model-specific instead of Llama-only
- add Qwen3 stop token handling

### Optional systems follow-up

If more systems polishing is needed:

- add Athena-on auto GEQO avoidance or threshold bump
- rerun flat JOB under that automatic policy instead of forcing `PGOPTIONS='-c geqo=off'`

## 11. Current Project Positioning

The strongest current story is:

- hint-based LLM optimizers are limited by external hint interfaces
- this work internalizes candidate generation and candidate application inside PostgreSQL 18.1
- the system handles an important nested-query subclass and flat JOB effectively
- the candidate set contains exact default and often much better alternatives
- cheap selectors are weak enough that an LLM reranker has real room to add value

Do not over-claim:

- this is not yet full general subquery rewrite-space exploration
- current V1 strength is non-correlated `FROM` subqueries plus flat queries

## 12. If Starting a New Session

Start from these files first:

1. [PG18_Athena_LLMOpt_step_by_step_plan.md](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/PG18_Athena_LLMOpt_step_by_step_plan.md)
2. [SESSION_HANDOFF_2026-03-10.md](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/SESSION_HANDOFF_2026-03-10.md)
3. [oracle_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/oracle_generated_job_v1_195q_real_imdb_bind2_k5_exactdefault_v1.json)
4. [oracle_job_flat_real_imdb_k5_exactdefault_geqo_off_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/oracle_job_flat_real_imdb_k5_exactdefault_geqo_off_v1.json)
5. [inference_vllm.py](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/LLMOpt/LLM_training/inference_vllm.py)

Then decide whether the next step is:

- Llama 3.1 fine-tuning and inference on the latest datasets
- Qwen3 compatibility upgrade
- or GEQO avoidance hardening in PostgreSQL
