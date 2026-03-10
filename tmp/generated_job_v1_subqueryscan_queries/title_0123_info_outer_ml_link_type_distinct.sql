SELECT
    t.id AS title_id,
    t.title,
    t.production_year,
    it.info AS outer_text,
    mi.info_type_id AS outer_num
FROM
    title t
    JOIN movie_info mi ON mi.movie_id = t.id
    JOIN info_type it ON it.id = mi.info_type_id
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
    t.kind_id IS NOT NULL
    AND it.info IS NOT NULL
ORDER BY
    1, 2
;