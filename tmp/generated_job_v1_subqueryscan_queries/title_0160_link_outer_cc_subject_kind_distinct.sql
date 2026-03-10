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
                cc2.movie_id AS join_key,
                cct2.kind AS payload_text,
                cc2.subject_id AS payload_num
            FROM complete_cast cc2
            JOIN comp_cast_type cct2 ON cct2.id = cc2.subject_id
            WHERE cct2.kind IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = t.id
WHERE
    t.imdb_id IS NOT NULL
    AND lt.link IS NOT NULL
ORDER BY
    1, 2
;