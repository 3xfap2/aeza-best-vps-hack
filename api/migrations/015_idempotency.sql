-- Ключи идемпотентности для POST (заголовок Idempotency-Key): повтор запроса с тем же ключом
-- возвращает сохранённый ответ и не выполняется второй раз. Ключ живёт сутки и привязан к вызывающему.
BEGIN;

CREATE TABLE idempotency_keys (
    scope text NOT NULL,            -- хеш токена или сессии вызывающего
    key text NOT NULL,
    request_hash text NOT NULL,     -- метод + путь + тело: тот же ключ с другим запросом — ошибка
    status_code integer,            -- NULL, пока первый запрос выполняется
    response_body bytea,
    content_type text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, key)
);

COMMIT;
