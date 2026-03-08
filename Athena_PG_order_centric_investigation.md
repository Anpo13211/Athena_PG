# Athena_PG 調査メモ

対象:

- リポジトリ: `Athena_PG`
- 論文: `/Users/an/Desktop/papers/SIGMOD/2025/Athena.pdf`
- 主題: "JOIN 順序を中心に複数の実行計画候補を取り出して、Athena 側で扱えるようにする PostgreSQL 側コンポーネント" の実装

## 1. 結論

このリポジトリは、論文 Athena の 3 コンポーネント全部を含むフル実装ではなく、主に PostgreSQL 16.1 を fork して以下を足したものになっている。

- order-centric plan explorer の PostgreSQL 側実装
- 比較対象としての Lero 風の cardinality swing 実装
- それらを有効化する GUC と planner へのフック

逆に、この repo の中には論文 Section 5/6 の中心である以下は見当たらない。

- Tree-Mamba plan comparator
- time-weighted model trainer
- 学習、推論、plan ranking を行う Python / PyTorch 側コード

したがって、この repo は Athena 全体のうち「DBMS 内で candidate plan を作って取り出す部分」に相当すると読むのが自然である。

## 2. 論文と repo の対応

論文側の要点:

- Athena は 3 つの主要コンポーネントから成る: order-centric plan explorer, Tree-Mamba plan comparator, time-weighted model trainer
- order-centric explorer は diverse な join order を明示的に探索する
- bottom-up optimizer では "last join operation" を決める地点で hook できる
- Athena の explorer は single-pass で candidate plans を生成する

根拠:

- `Athena.pdf` p.7-8, Section 4
- `Athena.pdf` p.20-21, Section 7.3.1

repo 側の対応:

| 論文要素 | repo にあるか | 実装/痕跡 |
| --- | --- | --- |
| order-centric plan explorer | ある | `src/backend/optimizer/jop/jop_extension.c`, `planner.c`, `joinpath.c`, `allpaths.c`, `pathnode.c` |
| Lero との比較用 explorer | ある | `src/backend/lero/lero_extension.c`, `costsize.c`, `planner.c`, `guc_tables.c` |
| Tree-Mamba comparator | 見当たらない | repo 内に Mamba / model inference / featurizer 実装なし |
| time-weighted loss trainer | 見当たらない | repo 内に training / loss 実装なし |
| external execution control | repo 内には薄い | `README.md` で `pg_hint_plan` と `pg_prewarm` を要求 |

## 3. PostgreSQL planner のどこに差し込んでいるか

PostgreSQL optimizer の大まかな流れは `src/backend/optimizer/README:554-599` に整理されている。

```
planner()
  -> subquery_planner()
    -> grouping_planner()
      -> query_planner()
        -> make_one_rel()
          -> standard_join_search()
            -> join_search_one_level()
              -> add_paths_to_joinrel()
```

Athena_PG の独自フックは主にここに入っている。

1. `planner()` の入口で `enable_lero` を確認
2. `standard_join_search()` の前後でフラグ制御
3. `add_paths_to_joinrel()` の root join 候補生成時に pathlist を退避/復元
4. `add_path()` の path pruning を後段で緩める
5. `standard_planner()` の最後で `final_rel->pathlist` をファイルへ保存

関連箇所:

- `src/backend/optimizer/plan/planner.c:275-285`
- `src/backend/optimizer/plan/planner.c:417-425`
- `src/backend/optimizer/path/allpaths.c:3424-3505`
- `src/backend/optimizer/path/joinpath.c:155-165`
- `src/backend/optimizer/path/joinpath.c:374-384`
- `src/backend/optimizer/util/pathnode.c:443-458`
- `src/backend/optimizer/jop/jop_extension.c:189-202`

## 4. order-centric explorer 実装の読み解き

### 4.1 有効化スイッチ

GUC `enable_join_order_plans` が追加されている。

- 宣言: `src/backend/optimizer/path/costsize.c:158-159`
- extern: `src/include/optimizer/cost.h:74-75`
- GUC 登録: `src/backend/utils/misc/guc_tables.c:2012-2018`

つまり、この機能は extension ではなく PostgreSQL 本体に bool GUC を追加して有効化する作りである。

### 4.2 論文の "last join operation で hook" と対応する箇所

論文 Section 4.2 では、bottom-up optimizer では "last join operation" を決める地点で hook する、と説明している。コード上ではこれに相当する処理が `add_paths_to_joinrel()` に入っている。

該当コード:

- `src/backend/optimizer/path/joinpath.c:157-165`

要点:

- `joinrelids` が `root->all_baserels` と一致するとき、その `joinrel` は query 全体を覆う top-level join relation だとみなしている
- そのとき `joinrel->pathlist` を一旦 `tmp_list` に退避し、新しい候補だけを積み直せるように `NIL` にしている

復元処理:

- `src/backend/optimizer/path/joinpath.c:374-384`

ここで、今回の `add_paths_to_joinrel()` 呼び出しで生成した `joinrel->pathlist` を、退避していた `tmp_list` に append し直している。

この設計から分かること:

- 実装者は "root joinrel に対する path 候補を、各 `add_paths_to_joinrel()` 呼び出しをまたいで保持する" ことを狙っている
- つまり Athena 論文の "top-level / root node の join order を列挙する" という主張にかなり素直に対応している

### 4.3 なぜ `joinpath.c` だけでは足りず、`allpaths.c` と `pathnode.c` も変えているのか

`joinpath.c` の stash/restore だけでは不十分で、後段の upper rel 生成で path が pruning されると最終候補が消える可能性がある。そのため 2 つ追加変更が入っている。

#### A. `standard_join_search()` の前後でフラグを切り替える

- `src/backend/optimizer/path/allpaths.c:3424-3425`
- `src/backend/optimizer/path/allpaths.c:3504-3505`

ここで `save_join_order_plan_finished` を false -> true にしている。

意味:

- join search 中か
- join search が終わって upper rel を積み上げる段階か

を区別するためのフラグとして使っている。

#### B. `add_path()` 側で fuzzy cost comparison を無効化する

- `src/backend/optimizer/util/pathnode.c:454-458`

`enable_join_order_plans && save_join_order_plan_finished` のとき、`costcmp = COSTS_DIFFERENT` に固定している。これは実質的に「既存 path と new path をコスト同値扱いせず、path pruning をかなり抑える」という意味になる。

これにより:

- root joinrel で残した複数 candidate が
- その後の upper rel (`ORDER BY`, `LIMIT`, `Agg`, `Distinct` など) 生成の過程で潰れにくくなり
- `final_rel->pathlist` まで複数候補が届く

という構造になっている。

### 4.4 planner 最後で候補をファイルへ吐く

`standard_planner()` の最後で `final_rel = fetch_upper_rel(root, UPPERREL_FINAL, NULL)` を取った直後、`enable_join_order_plans` が真なら `save_join_order_plans(root, final_rel->pathlist)` を呼ぶ。

該当コード:

- `src/backend/optimizer/plan/planner.c:420-425`

ここがこの repo における candidate export の最終出口である。

### 4.5 join order の文字列化

`src/backend/optimizer/jop/jop_extension.c` が、Path 木を join order 文字列へ変換する。

重要点:

- leaf は table alias 名にする: `SeqScan`, `IndexScan`, `IndexOnlyScan`, `BitmapHeapScan`
- binary join は `(outer inner)` という括弧表現にする
- unary wrapper (`Material`, `Sort`, `Agg`, `Gather`, `GatherMerge`, `Memoize`, `ProjectionPath`) は剥がして下に降りる
- 最後に `/tmp/Athena_join_order_plans.txt` に 1 path 1 行で保存する

該当コード:

- `src/backend/optimizer/jop/jop_extension.c:34-186`
- `src/backend/optimizer/jop/jop_extension.c:189-202`

この表現は "physical plan 全体" ではなく "join tree の骨格" を外に出すためのものと解釈できる。operator type や cost はここでは出していない。

想定出力イメージ:

```text
((A B) (C D))
(((A C) B) D)
```

なお、これはコードからの推測であり、repo 内にはこのファイルを消費するコードは見当たらない。

## 5. 論文の主張と実装の対応

### 5.1 "single pass" か

論文 p.8, p.21 では Athena explorer の利点として "traditional query optimization procedure の single pass で multiple candidate plans を作れる" と述べている。

コードとの対応はかなり明確である。

- `enable_join_order_plans` 側では、planner を何度も呼び直していない
- 既存の `standard_join_search()` と `add_paths_to_joinrel()` の途中結果を保持し、そのまま `final_rel->pathlist` へ流している

この意味で、JOP 側の実装は論文の single-pass 主張と整合している。

### 5.2 "top-level / root node を列挙する" か

論文 p.8 では、Athena は join order enumeration を top-level (`root node`) に限定すると説明している。

コードでも:

- `root->all_baserels`
- 現在の `joinrelids`

が一致した時だけ特別扱いするので、実装意図はまさに root-level hook である。

対応箇所:

- `src/backend/optimizer/path/joinpath.c:157-165`

### 5.3 "last join operation で hook" か

論文 p.8 の "bottom-up optimizer では last join operation を決める地点で hook" という記述は、実装上は `add_paths_to_joinrel()` で root joinrel を見分けて candidate path を保持する形で表現されている。

その意味で、論文の説明とコードのフック位置はほぼ一致している。

## 6. Lero 風実装の読み解き

Athena 論文は related work / baseline として Lero を繰り返し参照しているが、この repo には Lero 風の比較実装も含まれている。

### 6.1 入口

`planner()` の先頭で `enable_lero` が真なら `planner_hook` より先に `lero_pgsysml_hook_planner()` を呼ぶ。

- `src/backend/optimizer/plan/planner.c:280-283`

つまり `enable_lero` は通常の planner path を乗っ取る。

### 6.2 join cardinality 変更の実際

`set_joinrel_size_estimates()` で core の rows estimate を計算した後、`enable_lero` が真なら `lero_pgsysml_set_joinrel_size_estimates()` を追加で呼んで `rel->rows` を上書きする。

- `src/backend/optimizer/path/costsize.c:5090-5100`

`lero_extension.c` の流れ:

1. 1 回目の `standard_planner(copyObject(parse), ...)` で元の cardinality を記録
2. 各 joinrel が何テーブル入力かも数える
3. `join_input_table_nums[i] == lero_subquery_table_num` の箇所だけ `original_card * lero_swing_factor` に変更
4. 元の parse でもう 1 回 `standard_planner()` を呼ぶ

該当コード:

- `src/backend/lero/lero_extension.c:149-183`
- `src/backend/lero/lero_extension.c:185-207`

この実装は、論文が説明する "same number of tables を持つ subqueries の cardinality に同じ multiplier を掛ける" Lero 風挙動と概ね一致している。

### 6.3 GUC

- `enable_lero`: `src/backend/utils/misc/guc_tables.c:2002-2008`
- `lero_subquery_table_num`: `src/backend/utils/misc/guc_tables.c:3530-3536`
- `lero_swing_factor`: `src/backend/utils/misc/guc_tables.c:3821-3827`

## 7. repo 境界: Athena 全体ではなく PostgreSQL 側コンポーネント

repo 全体を検索しても、Tree-Mamba / TaiLr / model inference / training に相当するコードは見当たらない。したがって以下の役割分担が推定される。

- この repo: PostgreSQL fork と candidate plan export
- 別の repo / 別プロセス: candidate plan の評価、学習、ランキング、最終 plan 選択

README に `pg_hint_plan` と `pg_prewarm` の導入が書かれていることから、外部側が:

- 生成した join order candidate を何らかの hint に変換し
- 特定 plan を強制し
- `pg_prewarm` でキャッシュ条件を整えつつ
- 実行時間を収集

する構成だった可能性が高い。これは README からの推測であり、repo 内に消費側コードはない。

根拠:

- `README.md:39-72`
- `rg` では `/tmp/Athena_join_order_plans.txt` を読むコードは repo 内に存在しない

## 8. 実装上の注意点・気になる点

### 8.1 root join 判定が `Bitmapset.words[0]` 依存

`joinpath.c` では root 判定に以下を使っている。

```c
int root_relids = (int) root->all_baserels->words[0];
int join_relids = (int) joinrelids->words[0];
if (root_relids == join_relids) ...
```

該当箇所:

- `src/backend/optimizer/path/joinpath.c:159-161`

これは `Bitmapset` の最初の 1 word しか見ていないので、relid 数が多い query では厳密でない可能性がある。より堅い書き方は `bms_equal()` などである。

### 8.2 `catch_join_order()` が未対応 path type に弱い

`jop_extension.c` は `SubqueryScan`, `Append`, `Limit`, `WindowAgg`, `ModifyTable` などを十分には処理していない。未対応 node は warning / error になる。

- `src/backend/optimizer/jop/jop_extension.c:153-179`

そのため、複雑な query では join order 抽出が壊れるか、空文字列に近い出力になる可能性がある。

### 8.3 duplicate plan / duplicate join order の可能性

`save_join_order_plans()` は `final_rel->pathlist` をそのまま書き出し、dedup をしない。また `catch_join_order()` は unary operator を剥がして join skeleton だけを残すので、異なる physical plan が同じ join order 文字列に潰れることがある。

結果:

- 同じ join order が複数行出る可能性がある
- operator diversity は出力ファイルでは見えない

### 8.4 出力先が固定ファイル

`/tmp/Athena_join_order_plans.txt` を毎回 `w` で開いて上書きしている。

- `src/backend/optimizer/jop/jop_extension.c:193`

このため:

- session ごとに分離されない
- 並行 query では競合しうる
- 実験用実装としては十分でも、汎用 DB 機能としては荒い

### 8.5 `enable_lero` が `planner_hook` をバイパスする

`planner()` では `enable_lero` の分岐が `planner_hook` より先にある。

- `src/backend/optimizer/plan/planner.c:280-283`

したがって、別の planner hook を使う extension と併用すると相互作用が分かりにくくなる。

### 8.6 GUC 定義に copy-paste 由来と思われる不整合がある

`lero_subquery_table_num` の default は `SCRAM_SHA_256_DEFAULT_ITERATIONS` になっており、実体は 4096。

- GUC 側: `src/backend/utils/misc/guc_tables.c:3530-3536`
- macro 定義: `src/include/common/scram-common.h:50`

これは実装意図から見ると不自然で、copy-paste ミスの可能性が高い。

また `lero_swing_factor` の説明文も別 GUC 由来の文言になっている。

- `src/backend/utils/misc/guc_tables.c:3821-3827`

## 9. コミット履歴から見える実装意図

`3edc6580c0` (PostgreSQL 16.1 stamp) 以降の履歴を見ると、Athena_PG 独自実装はかなり明確に 2 系統へ分かれている。

- `4650227dfb Feat: Implement Lero's plan enumration.`
- `ab538e5bd6 Fix: Enable lero hook`
- `4b29049063 Fix: Wrong number of tables and possible memory problem.`
- `ae0b0aad95 Feat: Implement join order plans enumeration`
- `3a01bb07f8 Fix: correct the output join order of JOP`
- `8e43d32097 Fix: correct the output join order, recognize T_Result path`

この履歴からも、この repo の中心は:

- Lero 風 baseline
- join order plan enumeration

の 2 本であることが確認できる。

## 10. まとめ

この repo の "Athena らしさ" は、PostgreSQL planner の root join decision に割り込んで candidate plans を single-pass で保持し、最終的に join order 文字列として外へ出す点にある。

実装の核は 4 つ:

1. `joinpath.c` で root joinrel の pathlist を stash/restore する
2. `allpaths.c` で join search の終了をフラグ化する
3. `pathnode.c` で後段 pruning を緩める
4. `planner.c` と `jop_extension.c` で final candidate を保存する

論文 Section 4 の "bottom-up optimizer の last join operation に hook し、top-level join order を single-pass で列挙する" という説明と、現在のコードはおおむね整合している。

ただし、この repo だけでは Athena 全体は完結しない。Tree-Mamba と学習器は別系統にあるはずで、この repo はあくまで PostgreSQL 側の candidate explorer と見るのが正確である。
