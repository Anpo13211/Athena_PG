SELECT
    t.id AS title_id,
    t.title,
    t.production_year,
    lt.link AS outer_text,
    ml.linked_movie_id AS outer_num
FROM
    title t
    JOIN movie_link ml ON ml.movie_id = t.id
    JOIN link_type lt ON lt.id = ml.link_type_id
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
    t.kind_id IS NOT NULL
    AND lt.link IS NOT NULL
ORDER BY
    1, 2
;