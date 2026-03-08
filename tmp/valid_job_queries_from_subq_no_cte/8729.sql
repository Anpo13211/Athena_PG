SELECT
    a.name AS actor_name,
    t.title AS movie_title,
    c.nr_order AS cast_order,
    m.name AS production_company,
    k.keyword AS movie_keyword,
    unique_years.production_year
FROM
    aka_name a
JOIN
    cast_info c ON a.person_id = c.person_id
JOIN
    title t ON c.movie_id = t.id
LEFT JOIN
    movie_companies mc ON t.id = mc.movie_id
LEFT JOIN
    company_name m ON mc.company_id = m.id
LEFT JOIN
    movie_keyword mk ON t.id = mk.movie_id
LEFT JOIN
    keyword k ON mk.keyword_id = k.id
JOIN
    (SELECT DISTINCT production_year FROM aka_title WHERE production_year IS NOT NULL) AS unique_years ON unique_years.production_year = t.production_year
WHERE
    t.production_year >= 2000
ORDER BY
    a.name, t.production_year DESC, c.nr_order;
