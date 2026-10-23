-- Чаты Telegram, подписанные на уведомления (подключаются командой /start <код>).
BEGIN;

CREATE TABLE telegram_subscribers (
    chat_id bigint PRIMARY KEY,
    username text,
    created_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;
