SELECT
    n.id AS person_id,
    n.name,
    n.gender,
    an.imdb_index AS outer_text,
    an.person_id AS outer_num
FROM
    name n
    JOIN aka_name an ON an.person_id = n.id
JOIN
    (
        SELECT *
        FROM (
            SELECT DISTINCT
                ci2.person_id AS join_key,
                t2.title AS payload_text,
                ci2.movie_id AS payload_num
            FROM cast_info ci2
            JOIN title t2 ON t2.id = ci2.movie_id
            WHERE t2.title IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = n.id
WHERE
    n.imdb_index IS NOT NULL
    AND an.imdb_index IS NOT NULL
ORDER BY
    1, 2
;