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
                an2.person_id AS join_key,
                an2.imdb_index AS payload_text,
                an2.id AS payload_num
            FROM aka_name an2
            WHERE an2.imdb_index IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = n.id
WHERE
    n.imdb_index IS NOT NULL
    AND it.info IS NOT NULL
ORDER BY
    1, 2
;