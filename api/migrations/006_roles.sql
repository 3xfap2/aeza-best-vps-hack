-- Роли команды и сумма под угрозой в момент отметки — для фактической отдачи от удержания.
BEGIN;

-- admin — всё, включая данные и команду; marketer — модель и работа с клиентами; viewer — только просмотр.
ALTER TABLE app_users ADD COLUMN role text NOT NULL DEFAULT 'marketer'
    CHECK (role IN ('admin', 'marketer', 'viewer'));

-- Ожидаемая потеря клиента (евроценты) в момент отметки: «продлил» превращает её в сохранённую выручку.
ALTER TABLE retention_actions ADD COLUMN at_risk bigint;

COMMIT;
