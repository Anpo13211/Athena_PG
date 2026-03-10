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
                an2.person_id AS join_key,
                an2.imdb_index AS payload_text,
                an2.id AS payload_num
            FROM aka_name an2
            WHERE an2.imdb_index IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = n.id
WHERE
    n.imdb_id IS NOT NULL
    AND t.title IS NOT NULL
ORDER BY
    1, 2
;