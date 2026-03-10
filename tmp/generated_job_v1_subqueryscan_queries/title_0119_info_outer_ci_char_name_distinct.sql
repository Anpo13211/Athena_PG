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
    t.kind_id IS NOT NULL
    AND it.info IS NOT NULL
ORDER BY
    1, 2
;