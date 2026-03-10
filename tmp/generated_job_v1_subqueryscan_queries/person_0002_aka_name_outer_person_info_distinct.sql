SELECT
    n.id AS person_id,
    n.name,
    n.gender,
    an.imdb_index AS outer_text,
    an.person_id AS outer_num
FROM
    name n
    JOIN aka_name an ON an.person_id = n.id
JOIN
    (
        SELECT *
        FROM (
            SELECT DISTINCT
                pi2.person_id AS join_key,
                it2.info AS payload_text,
                pi2.info_type_id AS payload_num
            FROM person_info pi2
            JOIN info_type it2 ON it2.id = pi2.info_type_id
            WHERE it2.info IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = n.id
WHERE
    n.imdb_id IS NOT NULL
    AND an.imdb_index IS NOT NULL
ORDER BY
    1, 2
;