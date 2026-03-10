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
                mc2.movie_id AS join_key,
                cn2.country_code AS payload_text,
                mc2.company_id AS payload_num
            FROM movie_companies mc2
            JOIN company_name cn2 ON cn2.id = mc2.company_id
            WHERE cn2.country_code IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = t.id
WHERE
    t.imdb_id IS NOT NULL
    AND it.info IS NOT NULL
ORDER BY
    1, 2
;