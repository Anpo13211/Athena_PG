SELECT
    n.id AS person_id,
    n.name,
    n.gender,
    t.title AS outer_text,
    ci.movie_id AS outer_num
FROM
    name n
    JOIN cast_info ci ON ci.person_id = n.id
    JOIN title t ON t.id = ci.movie_id
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
    AND t.title IS NOT NULL
ORDER BY
    1, 2
;