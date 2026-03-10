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
                ci2.movie_id AS join_key,
                an2.name AS payload_text,
                ci2.person_id AS payload_num
            FROM cast_info ci2
            JOIN aka_name an2 ON an2.person_id = ci2.person_id
            WHERE an2.name IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = t.id
WHERE
    t.kind_id IS NOT NULL
    AND lt.link IS NOT NULL
ORDER BY
    1, 2
;