SET lock_timeout = '2s';
ALTER TABLE users ADD CONSTRAINT chk_age CHECK (age >= 0) NOT VALID;
