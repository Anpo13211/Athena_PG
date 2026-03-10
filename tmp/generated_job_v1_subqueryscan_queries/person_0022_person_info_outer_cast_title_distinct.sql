SELECT
    n.id AS person_id,
    n.name,
    n.gender,
    it.info AS outer_text,
    pi.info_type_id AS outer_num
FROM
    name n
    JOIN person_info pi ON pi.person_id = n.id
    JOIN info_type it ON it.id = pi.info_type_id
JOIN
    (
        SELECT *
        FROM (
            SELECT DISTINCT
                ci2.person_id AS join_key,
                t2.title AS payload_text,
                ci2.movie_id AS payload_num
            FROM cast_info ci2
            JOIN title t2 ON t2.id = ci2.movie_id
            WHERE t2.title IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = n.id
WHERE
    n.gender IS NOT NULL
    AND it.info IS NOT NULL
ORDER BY
    1, 2
;