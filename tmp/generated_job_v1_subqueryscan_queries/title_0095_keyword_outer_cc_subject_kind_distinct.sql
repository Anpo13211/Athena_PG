SELECT
    t.id AS title_id,
    t.title,
    t.production_year,
    k.keyword AS outer_text,
    mk.keyword_id AS outer_num
FROM
    title t
    JOIN movie_keyword mk ON mk.movie_id = t.id
    JOIN keyword k ON k.id = mk.keyword_id
JOIN
    (
        SELECT *
        FROM (
            SELECT DISTINCT
                cc2.movie_id AS join_key,
                cct2.kind AS payload_text,
                cc2.subject_id AS payload_num
            FROM complete_cast cc2
            JOIN comp_cast_type cct2 ON cct2.id = cc2.subject_id
            WHERE cct2.kind IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = t.id
WHERE
    t.kind_id IS NOT NULL
    AND k.keyword IS NOT NULL
ORDER BY
    1, 2
;