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
                ci2.movie_id AS join_key,
                an2.name AS payload_text,
                ci2.person_id AS payload_num
            FROM cast_info ci2
            JOIN aka_name an2 ON an2.person_id = ci2.person_id
            WHERE an2.name IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = t.id
WHERE
    t.imdb_id IS NOT NULL
    AND k.keyword IS NOT NULL
ORDER BY
    1, 2
;