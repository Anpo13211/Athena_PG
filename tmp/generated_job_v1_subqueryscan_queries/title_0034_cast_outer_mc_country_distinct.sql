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
    t.production_year BETWEEN 1990 AND 2010
    AND n.name IS NOT NULL
ORDER BY
    1, 2
;