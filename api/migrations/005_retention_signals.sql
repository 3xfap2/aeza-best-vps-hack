-- Работа маркетолога с клиентом: что сделали и чем закончилось (причина ухода — отдельным полем).
BEGIN;

CREATE TABLE retention_actions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL,
    kind text NOT NULL CHECK (kind IN ('called', 'no_answer', 'messaged', 'offer', 'renewed', 'churned')),
    reason text,
    note text,
    author text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX retention_actions_account_idx ON retention_actions (account_id, id DESC);

-- Дополнительные сигналы оттока из других систем (поддержка, мониторинг, тарифы).
-- Необязательные: схема кейса только расширяется, без них всё работает как раньше.
ALTER TABLE accounts ADD COLUMN signals jsonb NOT NULL DEFAULT '{}';
ALTER TABLE services ADD COLUMN signals jsonb NOT NULL DEFAULT '{}';

COMMIT;
