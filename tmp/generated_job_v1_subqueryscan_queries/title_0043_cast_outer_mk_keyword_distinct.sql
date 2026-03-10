SELECT
    t.id AS title_id,
    t.title,
    t.production_year,
    n.name AS outer_text,
    ci.person_id AS outer_num
FROM
    title t
    JOIN cast_info ci ON ci.movie_id = t.id
    JOIN name n ON n.id = ci.person_id
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
    t.kind_id IS NOT NULL
    AND n.name IS NOT NULL
ORDER BY
    1, 2
;