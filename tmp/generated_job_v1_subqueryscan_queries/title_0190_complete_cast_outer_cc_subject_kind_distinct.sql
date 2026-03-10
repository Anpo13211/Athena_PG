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
    t.production_year BETWEEN 1990 AND 2010
    AND cct.kind IS NOT NULL
ORDER BY
    1, 2
;