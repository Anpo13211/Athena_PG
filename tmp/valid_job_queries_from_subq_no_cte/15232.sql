SELECT 
    a.name AS actor_name,
    t.title AS movie_title,
    t.production_year,
    c.number_of_actors
FROM 
    aka_name a
JOIN 
    cast_info ci ON a.person_id = ci.person_id
JOIN 
    title t ON ci.movie_id = t.id
JOIN 
    (SELECT movie_id, COUNT(*) AS number_of_actors 
     FROM cast_info 
     GROUP BY movie_id) c ON t.id = c.movie_id
WHERE 
    t.production_year = 2020
ORDER BY 
    a.name;
