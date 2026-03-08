SELECT
    t.id AS title_id,
    t.title,
    t.production_year,
    tn.name AS title_name,
    m.name AS company_name,
    a.name AS actor_name
FROM
    title t
JOIN
    aka_title at ON t.id = at.movie_id
JOIN
    movie_companies mc ON t.id = mc.movie_id
JOIN
    company_name m ON mc.company_id = m.id
JOIN
    complete_cast cc ON t.id = cc.movie_id
JOIN
    cast_info ci ON cc.subject_id = ci.person_id
JOIN
    aka_name a ON ci.person_id = a.person_id
JOIN
    (SELECT DISTINCT name, imdb_index FROM char_name) tn ON t.imdb_index = tn.imdb_index
WHERE
    t.production_year >= 2000
ORDER BY
    t.production_year DESC, t.title;
