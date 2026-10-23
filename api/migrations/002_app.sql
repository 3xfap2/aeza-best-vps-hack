-- Таблицы приложения. Таблицы источника (accounts, services, transactions) — в 001 без изменений.
BEGIN;

CREATE TABLE app_users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    login text NOT NULL UNIQUE,
    password_hash text,
    display_name text NOT NULL,
    oidc_subject text UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_sessions (
    token_hash text PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX app_sessions_user_idx ON app_sessions (user_id);

-- Каждая версия настроек модели; действующая — последняя.
CREATE TABLE model_settings (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    data jsonb NOT NULL,
    changes jsonb NOT NULL DEFAULT '[]',
    author text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Загруженные снимки целиком: для «вернуть к дате снимка» и аудита.
CREATE TABLE source_snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    data jsonb NOT NULL,
    reference_at timestamptz,
    counts jsonb NOT NULL,
    loaded_by text NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);

-- Журнал смен статусов услуг: из него строится фактический отток.
CREATE TABLE status_transitions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    service_id bigint NOT NULL,
    account_id bigint NOT NULL,
    from_status text,
    to_status text NOT NULL,
    at timestamptz NOT NULL,
    source text NOT NULL CHECK (source IN ('snapshot', 'event', 'simulator')),
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX status_transitions_at_idx ON status_transitions (at);

-- Лента изменений для экрана «Данные».
CREATE TABLE activity_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL,
    summary text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}',
    author text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;
