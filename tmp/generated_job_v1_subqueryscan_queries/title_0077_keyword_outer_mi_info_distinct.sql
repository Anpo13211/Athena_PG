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
                mi2.movie_id AS join_key,
                it2.info AS payload_text,
                mi2.info_type_id AS payload_num
            FROM movie_info mi2
            JOIN info_type it2 ON it2.id = mi2.info_type_id
            WHERE it2.info IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = t.id
WHERE
    t.production_year >= 2000
    AND k.keyword IS NOT NULL
ORDER BY
    1, 2
;