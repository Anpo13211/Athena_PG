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
    t.imdb_id IS NOT NULL
    AND k.keyword IS NOT NULL
ORDER BY
    1, 2
;