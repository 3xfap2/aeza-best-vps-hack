-- История score клиентов: строка пишется только когда оценка или уровень изменились.
BEGIN;

CREATE TABLE score_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL,
    score integer,
    level text NOT NULL,
    as_of timestamptz NOT NULL,
    reason text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX score_history_account_idx ON score_history (account_id, id DESC);

COMMIT;
