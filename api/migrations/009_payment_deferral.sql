-- Перенос платежа (отсрочка) как инструмент удержания: отдельный вид отметки работы с клиентом.
BEGIN;

ALTER TABLE retention_actions DROP CONSTRAINT IF EXISTS retention_actions_kind_check;
ALTER TABLE retention_actions ADD CONSTRAINT retention_actions_kind_check
    CHECK (kind IN ('called', 'no_answer', 'messaged', 'offer', 'deferred', 'renewed', 'churned'));

COMMIT;
