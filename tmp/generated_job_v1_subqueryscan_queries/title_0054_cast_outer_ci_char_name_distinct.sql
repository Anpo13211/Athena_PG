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
                ch2.name AS payload_text,
                ci2.person_role_id AS payload_num
            FROM cast_info ci2
            JOIN char_name ch2 ON ch2.id = ci2.person_role_id
            WHERE ch2.name IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = t.id
WHERE
    t.production_year BETWEEN 1990 AND 2010
    AND n.name IS NOT NULL
ORDER BY
    1, 2
;