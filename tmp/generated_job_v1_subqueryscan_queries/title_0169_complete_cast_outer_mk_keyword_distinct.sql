SELECT
    t.id AS title_id,
    t.title,
    t.production_year,
    cct.kind AS outer_text,
    cc.subject_id AS outer_num
FROM
    title t
    JOIN complete_cast cc ON cc.movie_id = t.id
    JOIN comp_cast_type cct ON cct.id = cc.subject_id
JOIN
    (
        SELECT *
        FROM (
            SELECT DISTINCT
                mk2.movie_id AS join_key,
                k2.keyword AS payload_text,
                mk2.keyword_id AS payload_num
            FROM movie_keyword mk2
            JOIN keyword k2 ON k2.id = mk2.keyword_id
            WHERE k2.keyword IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = t.id
WHERE
    t.production_year >= 2000
    AND cct.kind IS NOT NULL
ORDER BY
    1, 2
;