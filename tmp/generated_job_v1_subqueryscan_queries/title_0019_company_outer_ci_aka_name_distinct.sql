SELECT
    t.id AS title_id,
    t.title,
    t.production_year,
    cn.name AS outer_text,
    mc.company_id AS outer_num
FROM
    title t
    JOIN movie_companies mc ON mc.movie_id = t.id
    JOIN company_name cn ON cn.id = mc.company_id
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
    AND cn.country_code IS NOT NULL
ORDER BY
    1, 2
;