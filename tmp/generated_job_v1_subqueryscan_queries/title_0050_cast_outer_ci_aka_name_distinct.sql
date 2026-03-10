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
    t.production_year BETWEEN 1990 AND 2010
    AND n.name IS NOT NULL
ORDER BY
    1, 2
;