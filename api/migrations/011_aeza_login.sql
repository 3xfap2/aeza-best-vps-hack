-- Вход через аккаунт Aéza: связь с ID аккаунта и статус «ждёт подтверждения» до решения администратора.
BEGIN;

ALTER TABLE app_users ADD COLUMN aeza_id text UNIQUE;
ALTER TABLE app_users DROP CONSTRAINT IF EXISTS app_users_role_check;
ALTER TABLE app_users ADD CONSTRAINT app_users_role_check CHECK (role IN ('admin', 'marketer', 'viewer', 'pending'));

COMMIT;
