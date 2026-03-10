SELECT
    t.id AS title_id,
    t.title,
    t.production_year,
    k.keyword AS outer_text,
    mk.keyword_id AS outer_num
FROM
    title t
    JOIN movie_keyword mk ON mk.movie_id = t.id
    JOIN keyword k ON k.id = mk.keyword_id
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
    AND k.keyword IS NOT NULL
ORDER BY
    1, 2
;