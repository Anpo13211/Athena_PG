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
    t.production_year BETWEEN 1990 AND 2010
    AND n.name IS NOT NULL
ORDER BY
    1, 2
;