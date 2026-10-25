-- Журнал безопасности: кто входил (и кто не смог), кому не хватило прав, кто менял команду и модель,
-- кто выгружал email клиентов. В отличие от activity_log (лента работы для всей команды) читает только администратор.
BEGIN;

CREATE TABLE audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now(),
    actor text NOT NULL,
    action text NOT NULL,
    target text,
    ip text,
    request_id text,
    details jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_log_action_idx ON audit_log (action, id DESC);

COMMIT;
