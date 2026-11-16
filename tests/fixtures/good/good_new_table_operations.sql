CREATE TABLE new_t (id int);
ALTER TABLE new_t ADD COLUMN c INT NOT NULL;
CREATE INDEX idx_new_t_c ON new_t(c);
