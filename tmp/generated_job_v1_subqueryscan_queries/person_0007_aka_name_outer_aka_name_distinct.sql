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
                an2.person_id AS join_key,
                an2.imdb_index AS payload_text,
                an2.id AS payload_num
            FROM aka_name an2
            WHERE an2.imdb_index IS NOT NULL
        ) base_subq
        OFFSET 0
    ) sq ON sq.join_key = n.id
WHERE
    n.gender IS NOT NULL
    AND an.imdb_index IS NOT NULL
ORDER BY
    1, 2
;