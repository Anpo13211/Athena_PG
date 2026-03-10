#!/usr/bin/env python3

import argparse
from pathlib import Path


TITLE_OUTER_BUNDLES = [
    {
        "name": "company_outer",
        "anchor_from": "title t",
        "joins": [
            "JOIN movie_companies mc ON mc.movie_id = t.id",
            "JOIN company_name cn ON cn.id = mc.company_id",
        ],
        "selects": [
            "t.id AS title_id",
            "t.title",
            "t.production_year",
            "cn.name AS outer_text",
            "mc.company_id AS outer_num",
        ],
        "bundle_predicate": "cn.country_code IS NOT NULL",
    },
    {
        "name": "cast_outer",
        "anchor_from": "title t",
        "joins": [
            "JOIN cast_info ci ON ci.movie_id = t.id",
            "JOIN name n ON n.id = ci.person_id",
        ],
        "selects": [
            "t.id AS title_id",
            "t.title",
            "t.production_year",
            "n.name AS outer_text",
            "ci.person_id AS outer_num",
        ],
        "bundle_predicate": "n.name IS NOT NULL",
    },
    {
        "name": "keyword_outer",
        "anchor_from": "title t",
        "joins": [
            "JOIN movie_keyword mk ON mk.movie_id = t.id",
            "JOIN keyword k ON k.id = mk.keyword_id",
        ],
        "selects": [
            "t.id AS title_id",
            "t.title",
            "t.production_year",
            "k.keyword AS outer_text",
            "mk.keyword_id AS outer_num",
        ],
        "bundle_predicate": "k.keyword IS NOT NULL",
    },
    {
        "name": "info_outer",
        "anchor_from": "title t",
        "joins": [
            "JOIN movie_info mi ON mi.movie_id = t.id",
            "JOIN info_type it ON it.id = mi.info_type_id",
        ],
        "selects": [
            "t.id AS title_id",
            "t.title",
            "t.production_year",
            "it.info AS outer_text",
            "mi.info_type_id AS outer_num",
        ],
        "bundle_predicate": "it.info IS NOT NULL",
    },
    {
        "name": "link_outer",
        "anchor_from": "title t",
        "joins": [
            "JOIN movie_link ml ON ml.movie_id = t.id",
            "JOIN link_type lt ON lt.id = ml.link_type_id",
        ],
        "selects": [
            "t.id AS title_id",
            "t.title",
            "t.production_year",
            "lt.link AS outer_text",
            "ml.linked_movie_id AS outer_num",
        ],
        "bundle_predicate": "lt.link IS NOT NULL",
    },
    {
        "name": "complete_cast_outer",
        "anchor_from": "title t",
        "joins": [
            "JOIN complete_cast cc ON cc.movie_id = t.id",
            "JOIN comp_cast_type cct ON cct.id = cc.subject_id",
        ],
        "selects": [
            "t.id AS title_id",
            "t.title",
            "t.production_year",
            "cct.kind AS outer_text",
            "cc.subject_id AS outer_num",
        ],
        "bundle_predicate": "cct.kind IS NOT NULL",
    },
]

TITLE_SUBQUERIES = [
    {
        "name": "mc_country_distinct",
        "sql": """
SELECT DISTINCT
    mc2.movie_id AS join_key,
    cn2.country_code AS payload_text,
    mc2.company_id AS payload_num
FROM movie_companies mc2
JOIN company_name cn2 ON cn2.id = mc2.company_id
WHERE cn2.country_code IS NOT NULL
""".strip(),
    },
    {
        "name": "mc_min_company",
        "sql": """
SELECT
    mc2.movie_id AS join_key,
    NULL::text AS payload_text,
    min(mc2.company_id) AS payload_num
FROM movie_companies mc2
GROUP BY mc2.movie_id
""".strip(),
    },
    {
        "name": "mk_keyword_distinct",
        "sql": """
SELECT DISTINCT
    mk2.movie_id AS join_key,
    k2.keyword AS payload_text,
    mk2.keyword_id AS payload_num
FROM movie_keyword mk2
JOIN keyword k2 ON k2.id = mk2.keyword_id
WHERE k2.keyword IS NOT NULL
""".strip(),
    },
    {
        "name": "mi_info_distinct",
        "sql": """
SELECT DISTINCT
    mi2.movie_id AS join_key,
    it2.info AS payload_text,
    mi2.info_type_id AS payload_num
FROM movie_info mi2
JOIN info_type it2 ON it2.id = mi2.info_type_id
WHERE it2.info IS NOT NULL
""".strip(),
    },
    {
        "name": "ci_aka_name_distinct",
        "sql": """
SELECT DISTINCT
    ci2.movie_id AS join_key,
    an2.name AS payload_text,
    ci2.person_id AS payload_num
FROM cast_info ci2
JOIN aka_name an2 ON an2.person_id = ci2.person_id
WHERE an2.name IS NOT NULL
""".strip(),
    },
    {
        "name": "ci_char_name_distinct",
        "sql": """
SELECT DISTINCT
    ci2.movie_id AS join_key,
    ch2.name AS payload_text,
    ci2.person_role_id AS payload_num
FROM cast_info ci2
JOIN char_name ch2 ON ch2.id = ci2.person_role_id
WHERE ch2.name IS NOT NULL
""".strip(),
    },
    {
        "name": "ml_link_type_distinct",
        "sql": """
SELECT DISTINCT
    ml2.movie_id AS join_key,
    lt2.link AS payload_text,
    ml2.linked_movie_id AS payload_num
FROM movie_link ml2
JOIN link_type lt2 ON lt2.id = ml2.link_type_id
WHERE lt2.link IS NOT NULL
""".strip(),
    },
    {
        "name": "cc_subject_kind_distinct",
        "sql": """
SELECT DISTINCT
    cc2.movie_id AS join_key,
    cct2.kind AS payload_text,
    cc2.subject_id AS payload_num
FROM complete_cast cc2
JOIN comp_cast_type cct2 ON cct2.id = cc2.subject_id
WHERE cct2.kind IS NOT NULL
""".strip(),
    },
]

TITLE_PREDICATES = [
    "t.production_year >= 2000",
    "t.production_year BETWEEN 1990 AND 2010",
    "t.kind_id IS NOT NULL",
    "t.imdb_id IS NOT NULL",
]

PERSON_OUTER_BUNDLES = [
    {
        "name": "aka_name_outer",
        "anchor_from": "name n",
        "joins": [
            "JOIN aka_name an ON an.person_id = n.id",
        ],
        "selects": [
            "n.id AS person_id",
            "n.name",
            "n.gender",
            "an.imdb_index AS outer_text",
            "an.person_id AS outer_num",
        ],
        "bundle_predicate": "an.imdb_index IS NOT NULL",
    },
    {
        "name": "person_info_outer",
        "anchor_from": "name n",
        "joins": [
            "JOIN person_info pi ON pi.person_id = n.id",
            "JOIN info_type it ON it.id = pi.info_type_id",
        ],
        "selects": [
            "n.id AS person_id",
            "n.name",
            "n.gender",
            "it.info AS outer_text",
            "pi.info_type_id AS outer_num",
        ],
        "bundle_predicate": "it.info IS NOT NULL",
    },
    {
        "name": "cast_info_outer",
        "anchor_from": "name n",
        "joins": [
            "JOIN cast_info ci ON ci.person_id = n.id",
            "JOIN title t ON t.id = ci.movie_id",
        ],
        "selects": [
            "n.id AS person_id",
            "n.name",
            "n.gender",
            "t.title AS outer_text",
            "ci.movie_id AS outer_num",
        ],
        "bundle_predicate": "t.title IS NOT NULL",
    },
]

PERSON_SUBQUERIES = [
    {
        "name": "person_info_distinct",
        "sql": """
SELECT DISTINCT
    pi2.person_id AS join_key,
    it2.info AS payload_text,
    pi2.info_type_id AS payload_num
FROM person_info pi2
JOIN info_type it2 ON it2.id = pi2.info_type_id
WHERE it2.info IS NOT NULL
""".strip(),
    },
    {
        "name": "person_info_min_type",
        "sql": """
SELECT
    pi2.person_id AS join_key,
    NULL::text AS payload_text,
    min(pi2.info_type_id) AS payload_num
FROM person_info pi2
GROUP BY pi2.person_id
""".strip(),
    },
    {
        "name": "aka_name_distinct",
        "sql": """
SELECT DISTINCT
    an2.person_id AS join_key,
    an2.imdb_index AS payload_text,
    an2.id AS payload_num
FROM aka_name an2
WHERE an2.imdb_index IS NOT NULL
""".strip(),
    },
    {
        "name": "cast_title_distinct",
        "sql": """
SELECT DISTINCT
    ci2.person_id AS join_key,
    t2.title AS payload_text,
    ci2.movie_id AS payload_num
FROM cast_info ci2
JOIN title t2 ON t2.id = ci2.movie_id
WHERE t2.title IS NOT NULL
""".strip(),
    },
]

PERSON_PREDICATES = [
    "n.gender IS NOT NULL",
    "n.imdb_id IS NOT NULL",
    "n.imdb_index IS NOT NULL",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate valid V1-style nested JOB queries with non-correlated FROM subqueries."
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--family", choices=["all", "title", "person"], default="all")
    return parser.parse_args()


def format_query(selects, anchor_from, joins, subquery_sql, join_key_expr, where_predicates):
    lines = [
        "SELECT",
        "    " + ",\n    ".join(selects),
        "FROM",
        f"    {anchor_from}",
    ]
    for join in joins:
        lines.append(f"    {join}")
    lines.extend(
        [
            "JOIN",
            "    (",
            "        SELECT *",
            "        FROM (",
            "            " + subquery_sql.replace("\n", "\n            "),
            "        ) base_subq",
            "        OFFSET 0",
            "    ) sq ON sq.join_key = " + join_key_expr,
            "WHERE",
            "    " + "\n    AND ".join(where_predicates),
            "ORDER BY",
            "    1, 2",
            ";",
        ]
    )
    return "\n".join(lines)


def generate_title_queries():
    queries = []
    counter = 1
    for bundle in TITLE_OUTER_BUNDLES:
        for subquery in TITLE_SUBQUERIES:
            for predicate in TITLE_PREDICATES:
                sql = format_query(
                    selects=bundle["selects"],
                    anchor_from=bundle["anchor_from"],
                    joins=bundle["joins"],
                    subquery_sql=subquery["sql"],
                    join_key_expr="t.id",
                    where_predicates=[predicate, bundle["bundle_predicate"]],
                )
                queries.append(
                    (
                        f"title_{counter:04d}_{bundle['name']}_{subquery['name']}.sql",
                        sql,
                    )
                )
                counter += 1
    return queries


def generate_person_queries():
    queries = []
    counter = 1
    for bundle in PERSON_OUTER_BUNDLES:
        for subquery in PERSON_SUBQUERIES:
            for predicate in PERSON_PREDICATES:
                sql = format_query(
                    selects=bundle["selects"],
                    anchor_from=bundle["anchor_from"],
                    joins=bundle["joins"],
                    subquery_sql=subquery["sql"],
                    join_key_expr="n.id",
                    where_predicates=[predicate, bundle["bundle_predicate"]],
                )
                queries.append(
                    (
                        f"person_{counter:04d}_{bundle['name']}_{subquery['name']}.sql",
                        sql,
                    )
                )
                counter += 1
    return queries


def main():
    args = parse_args()
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    queries = []
    if args.family in {"all", "title"}:
        queries.extend(generate_title_queries())
    if args.family in {"all", "person"}:
        queries.extend(generate_person_queries())

    for filename, sql in queries:
        (outdir / filename).write_text(sql)

    print(f"generated {len(queries)} queries in {outdir}")


if __name__ == "__main__":
    main()
