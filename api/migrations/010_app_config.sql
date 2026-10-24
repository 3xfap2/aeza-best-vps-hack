-- Настройки приложения, которые администратор меняет в панели (подключение ИИ и т. п.).
-- Секреты (ключ API) хранятся здесь и наружу через API не отдаются — только последние 4 символа.
BEGIN;

CREATE TABLE app_config (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;
