-- «Перезвонить в дату»: клиент возвращается в очередь «Сегодня», когда наступает дата (по дате расчёта).
-- score_at — score клиента в момент отметки: вместе с at_risk показывает, что изменилось после контакта.
BEGIN;

ALTER TABLE retention_actions ADD COLUMN follow_up date;
ALTER TABLE retention_actions ADD COLUMN score_at integer;

COMMIT;
