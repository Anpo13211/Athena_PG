# Athena Bind2-Aware Loss Analysis

Target artifact:
- [oracle_generated_job_v1_195q_real_imdb_bind2_aware_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/oracle_generated_job_v1_195q_real_imdb_bind2_aware_v1.json)
- [candidate_plans_generated_job_v1_195q_real_imdb_bind2_aware_v1.json](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/candidate_plans_generated_job_v1_195q_real_imdb_bind2_aware_v1.json)

## Headline

Current best non-LLM configuration is:
- single representation
- `athena_max_distinct_bindings_per_skeleton = 2`
- binding-aware top-`K`

Result on real IMDB / 195-query workload:
- `oracle_eligible_count = 175`
- `oracle_better_than_default_count = 148`
- `default_not_worse_than_oracle_count = 27`

This note analyzes the remaining `27` non-improving queries.

## Main Finding

The `27` losses split into two qualitatively different buckets.

### Bucket A. Protected-default mismatch: 19 / 27

For `19` of the `27` loss queries, the best candidate inside the Athena candidate set is already the candidate marked `is_default_baseline = true`.

That means:
- the candidate generator is not necessarily missing a better non-default candidate
- instead, the current "protected default" candidate is not strictly identical to the true PostgreSQL baseline plan from `enable_join_order_plans = off`

Observed pattern:
- `oracle_candidate_idx == default_baseline_idx` for `19 / 27`
- average gap in this bucket is about `72 ms`
- median gap is about `28 ms`

Representative queries:
- `title_0022_company_outer_ci_char_name_distinct.sql`: `+519.776 ms`
- `title_0146_link_outer_ci_aka_name_distinct.sql`: `+288.754 ms`
- `title_0098_info_outer_mc_country_distinct.sql`: `+119.644 ms`

Interpretation:
- the current protected baseline comes from the Athena-enabled run's internal `best_path`
- this is not a strong enough invariant if the paper claim is "candidate set never drops below default"

### Bucket B. Genuine candidate miss: 8 / 27

For only `8` queries, the best candidate in the Athena set is a non-default candidate and still loses to the true PostgreSQL baseline.

These are:
- `person_0009_aka_name_outer_aka_name_distinct.sql`
- `title_0012_company_outer_mk_keyword_distinct.sql`
- `title_0014_company_outer_mi_info_distinct.sql`
- `title_0100_info_outer_mc_country_distinct.sql`
- `title_0112_info_outer_mi_info_distinct.sql`
- `title_0124_info_outer_ml_link_type_distinct.sql`
- `title_0144_link_outer_mi_info_distinct.sql`
- `title_0156_link_outer_ml_link_type_distinct.sql`

Observed severity:
- `6 / 8` have gap `<= 10 ms`
- `7 / 8` have gap `<= 50 ms`
- only one clearly material miss remains:
  - `title_0014_company_outer_mi_info_distinct.sql`: `+184.975 ms`

Interpretation:
- after `bind=2` and binding-aware truncation, the remaining true misses are relatively small
- the larger remaining issues are no longer about binding collapse

## Family-Level Pattern

Loss-family distribution:
- `mi_info_distinct`: `6`
- `mc_country_distinct`: `5`
- `person_info_distinct`: `4`
- `aka_name_distinct`: `3`
- `ci_char_name_distinct`: `3`
- `mk_keyword_distinct`: `2`
- `ml_link_type_distinct`: `2`
- `ci_aka_name_distinct`: `1`
- `cc_subject_kind_distinct`: `1`

Split by bucket:

Protected-default mismatch (`19`):
- `person_info_distinct`: `4`
- `mc_country_distinct`: `4`
- `mi_info_distinct`: `3`
- `ci_char_name_distinct`: `3`
- `aka_name_distinct`: `2`
- `mk_keyword_distinct`: `1`
- `ci_aka_name_distinct`: `1`
- `cc_subject_kind_distinct`: `1`

Genuine miss (`8`):
- `mi_info_distinct`: `3`
- `ml_link_type_distinct`: `2`
- `aka_name_distinct`: `1`
- `mk_keyword_distinct`: `1`
- `mc_country_distinct`: `1`

## Candidate-Set Characteristics

For the `27` loss queries:
- average `root_candidate_count = 14.33`
- average `bound_candidate_count = 28.00`
- average `binding_count = 2.89`

For the `148` improving queries:
- average `root_candidate_count = 14.73`
- average `bound_candidate_count = 25.68`
- average `binding_count = 2.70`

Implication:
- the remaining losses are **not** explained by lack of top-level order diversity
- they are also **not** explained by a collapse to a single binding profile anymore

## Operator Pattern

Oracle root join on loss queries:
- `Merge Join`: `14`
- `Hash Join`: `8`
- `Nested Loop`: `5`

Default-baseline root join on loss queries:
- `Merge Join`: `14`
- `Hash Join`: `7`
- `Nested Loop`: `6`

Default-baseline auxiliary operators are overrepresented on loss queries:
- `Sort`: `24 / 27`
- `Aggregate`: `23 / 27`
- `Gather Merge`: `15 / 27`

Compared with improving queries, loss queries are noticeably more likely to carry:
- `Sort`
- `Gather Merge`
- `Merge Join`

Interpretation:
- the remaining hard cases are disproportionately "ordered-output / merge-friendly" plans
- this is consistent with the generated workload shape: many queries use `DISTINCT` in the derived subquery and `ORDER BY` in the outer query

## Example: `title_0014_company_outer_mi_info_distinct.sql`

Query:
- [title_0014_company_outer_mi_info_distinct.sql](/Users/an/Desktop/bachelor/4thyear/2nd_semester/Athena_PG/tmp/generated_job_v1_subqueryscan_queries/title_0014_company_outer_mi_info_distinct.sql)

Observed runtimes:
- PostgreSQL default: `9225.872 ms`
- Athena oracle: `9410.847 ms`
- planner-min candidate: `9573.529 ms`

Important candidates:

Protected default:
- root: `Merge Join(Hash Join(Hash Join(mc, t), cn), sq)`
- operators: `Gather Merge`, `Sort`, `Aggregate`

Athena oracle winner:
- root: `Hash Join(Hash Join(mc, cn), Hash Join(sq, t))`
- all-hash structure

Interpretation:
- the true PostgreSQL default seems to benefit from an ordered / merge-aware shape
- current Athena selection still leans too aggressively toward non-default hash-heavy completions for this family

## What To Improve Next

### 1. Make the protected default exact

Highest-priority fix.

Current issue:
- the protected baseline candidate is taken from the Athena-enabled run
- this is not guaranteed to be the exact same executable plan as `enable_join_order_plans = off`

Why this matters:
- `19 / 27` losses are likely explained by this mismatch

Recommended fix:
- materialize the true baseline from an explicit `enable_join_order_plans = off` run
- inject it into the candidate set as a protected candidate
- keep it immune from truncation

Expected effect:
- this should directly reduce a large fraction of the remaining `27` losses

### 2. Add ordered-plan awareness to candidate budgeting

Current issue:
- the remaining genuine misses are concentrated in `Merge Join` / `Sort` / `Gather Merge` heavy families

Recommended fix:
- keep one protected merge-friendly / sorted-output candidate when it is distinct from the current cheapest candidate
- do this inside the same fixed `K`, not by increasing prompt length

Practical form:
- `default exact` candidate: `1`
- cheapest non-default candidate: `1`
- best sorted / merge-friendly candidate: `1`
- remaining slot: cost/binding-aware fill

Expected effect:
- should specifically target `mi_info_distinct`, `mc_country_distinct`, `ci_char_name_distinct`, `ml_link_type_distinct`

### 3. Keep binding-aware truncation; do not undo it

The current loss set is no longer dominated by binding collapse.

Evidence:
- average `binding_count` on loss queries is already `2.89`
- previous pre-`bind=2` loss regime had collapsed to one binding profile

Conclusion:
- binding-aware retention solved an earlier problem
- the next gains are elsewhere

## Bottom Line

The remaining `27` losses do **not** indicate that the current Athena candidate generator is fundamentally weak.

They indicate two narrower issues:
- the baseline-safety invariant is not yet strict enough (`19 / 27`)
- the remaining true misses are biased toward ordered / merge-friendly plans (`8 / 27`)

So the most defensible next step is:
1. exact default-baseline injection
2. sorted-output / merge-aware candidate protection inside the same fixed prompt budget

This is a much tighter target than "more join-order diversity" or "more bindings".
