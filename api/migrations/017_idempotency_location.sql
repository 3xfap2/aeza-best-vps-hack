-- Повтор по Idempotency-Key возвращает и Location созданного ресурса, а не только тело.
ALTER TABLE idempotency_keys ADD COLUMN location text;
