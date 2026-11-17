CREATE TABLE users (id serial primary key);
CREATE TABLE orders (id serial primary key, user_id int references users(id));
DROP TABLE users;
