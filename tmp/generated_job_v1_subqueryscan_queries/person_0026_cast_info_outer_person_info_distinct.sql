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
                pi2.person_id AS join_key,
                it2.info AS payload_text,
                pi2.info_type_id AS payload_num
            FROM person_info pi2
            JOIN info_type it2 ON it2.id = pi2.info_type_id
            WHERE it2.info IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = n.id
WHERE
    n.imdb_id IS NOT NULL
    AND t.title IS NOT NULL
ORDER BY
    1, 2
;