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
                mk2.movie_id AS join_key,
                k2.keyword AS payload_text,
                mk2.keyword_id AS payload_num
            FROM movie_keyword mk2
            JOIN keyword k2 ON k2.id = mk2.keyword_id
            WHERE k2.keyword IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = t.id
WHERE
    t.production_year BETWEEN 1990 AND 2010
    AND it.info IS NOT NULL
ORDER BY
    1, 2
;