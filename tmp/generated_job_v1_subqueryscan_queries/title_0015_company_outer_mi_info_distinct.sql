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
    AND cn.country_code IS NOT NULL
ORDER BY
    1, 2
;