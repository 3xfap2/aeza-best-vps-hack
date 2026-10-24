-- Очередь событий биллинга (входящие): сервис приёма на Go кладёт сюда события пачками и отвечает сразу,
-- API забирает очередь пачками и делает один пересчёт на пачку. Очередь переживает перезапуск API,
-- event_id защищает от повторной доставки одного события.
BEGIN;

CREATE TABLE event_inbox (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id text UNIQUE,
    payload jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    ok boolean,
    result text
);
CREATE INDEX event_inbox_pending_idx ON event_inbox (id) WHERE processed_at IS NULL;

COMMIT;
