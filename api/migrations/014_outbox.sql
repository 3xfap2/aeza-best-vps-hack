-- Исходящие события во внешние системы (CRM, вебхук) — transactional outbox.
-- Событие пишется в той же транзакции, что и действие в Радаре (отметка, отзыв), поэтому не теряется,
-- даже если внешняя система недоступна; фоновая доставка повторяет с растущей паузой.
BEGIN;

CREATE TABLE outbox (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sink text NOT NULL,
    type text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    last_error text
);
CREATE INDEX outbox_due_idx ON outbox (next_attempt_at) WHERE delivered_at IS NULL;

COMMIT;
