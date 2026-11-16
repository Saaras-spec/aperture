SET lock_timeout = '2s';
ALTER TABLE users ADD COLUMN age INT NOT NULL DEFAULT 0;
