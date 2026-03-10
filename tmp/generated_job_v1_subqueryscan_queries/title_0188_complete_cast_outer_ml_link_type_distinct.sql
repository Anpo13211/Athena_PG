SELECT
    t.id AS title_id,
    t.title,
    t.production_year,
    cct.kind AS outer_text,
    cc.subject_id AS outer_num
FROM
    title t
    JOIN complete_cast cc ON cc.movie_id = t.id
    JOIN comp_cast_type cct ON cct.id = cc.subject_id
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
    t.imdb_id IS NOT NULL
    AND cct.kind IS NOT NULL
ORDER BY
    1, 2
;