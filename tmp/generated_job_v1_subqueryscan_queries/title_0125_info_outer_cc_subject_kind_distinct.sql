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
    t.production_year >= 2000
    AND it.info IS NOT NULL
ORDER BY
    1, 2
;