SET lock_timeout = '2s';
CREATE TABLE users (id serial primary key);
CREATE TABLE orders (id serial primary key, user_id int references users(id));
ALTER TABLE orders DROP CONSTRAINT fk_orders_user_id;
DROP TABLE users;
