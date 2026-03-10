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
                ml2.movie_id AS join_key,
                lt2.link AS payload_text,
                ml2.linked_movie_id AS payload_num
            FROM movie_link ml2
            JOIN link_type lt2 ON lt2.id = ml2.link_type_id
            WHERE lt2.link IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = t.id
WHERE
    t.production_year >= 2000
    AND k.keyword IS NOT NULL
ORDER BY
    1, 2
;