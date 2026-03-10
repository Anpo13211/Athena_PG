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
    AND cct.kind IS NOT NULL
ORDER BY
    1, 2
;