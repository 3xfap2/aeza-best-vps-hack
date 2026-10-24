-- Обратная связь.
-- history_snapshots — прошлые снимки: модель оценивает их текущими весами и сверяет с тем, что случилось потом.
-- feedback — отзывы сотрудников о самом Радаре.
BEGIN;

CREATE TABLE history_snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reference_at timestamptz NOT NULL UNIQUE,
    data jsonb NOT NULL,
    source text NOT NULL CHECK (source IN ('demo', 'billing')),
    loaded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE feedback (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    author text NOT NULL,
    author_name text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('idea', 'problem', 'other')),
    text text NOT NULL,
    page text,
    status text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'done')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX feedback_created_idx ON feedback (id DESC);

COMMIT;
