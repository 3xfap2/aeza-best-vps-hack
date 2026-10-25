// Сервис приёма событий биллинга «Радара оттока».
//
// Биллинг шлёт события пачками: POST /api/v1/ingest/events. Сервис проверяет формат, одним запросом
// кладёт пачку в очередь event_inbox в PostgreSQL (повтор того же eventId отбрасывается) и сразу
// отвечает 202. API на Python забирает очередь пачками и делает один пересчёт на пачку — только
// затронутых клиентов. Поток событий не упирается в пересчёт модели, а очередь переживает перезапуск API.
package main

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

const (
	maxEvents = 10000
	maxBody   = 8 << 20
)

// Store — очередь событий. В проде PostgreSQL, в тестах — заглушка.
type Store interface {
	Insert(ctx context.Context, ids []*string, payloads []string) (int, error)
	Ping(ctx context.Context) error
}

type pgStore struct{ pool *pgxpool.Pool }

// Insert кладёт пачку одной транзакцией и будит API уведомлением radar_inbox; возвращает число новых событий.
func (s pgStore) Insert(ctx context.Context, ids []*string, payloads []string) (int, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck // после Commit откат ничего не делает
	var added int
	err = tx.QueryRow(ctx, `WITH ins AS (
		INSERT INTO event_inbox (event_id, payload)
		SELECT e, p::jsonb FROM unnest($1::text[], $2::text[]) AS u(e, p)
		ON CONFLICT (event_id) DO NOTHING RETURNING 1)
		SELECT count(*) FROM ins`, ids, payloads).Scan(&added)
	if err != nil {
		return 0, err
	}
	if _, err = tx.Exec(ctx, "SELECT pg_notify('radar_inbox', '')"); err != nil {
		return 0, err
	}
	return added, tx.Commit(ctx)
}

func (s pgStore) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

type server struct {
	store   Store
	token   string
	metrics *metrics
}

type accepted struct {
	Accepted   int `json:"accepted"`
	Duplicates int `json:"duplicates"`
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/v1/ingest/events", s.events)
	mux.HandleFunc("GET /api/v1/ingest/health", s.health)
	mux.HandleFunc("GET /health", s.health)
	mux.HandleFunc("GET /metrics", s.metrics.serve)
	return mux
}

func (s *server) events(w http.ResponseWriter, r *http.Request) {
	started := time.Now()
	code := s.handleEvents(w, r)
	s.metrics.request(code, time.Since(started))
}

func (s *server) handleEvents(w http.ResponseWriter, r *http.Request) int {
	auth := r.Header.Get("Authorization")
	if !strings.HasPrefix(auth, "Bearer ") || subtle.ConstantTimeCompare([]byte(auth[7:]), []byte(s.token)) != 1 {
		w.Header().Set("WWW-Authenticate", "Bearer")
		return reply(w, http.StatusUnauthorized, detail("Нужен Bearer-токен сервиса приёма (INGEST_TOKEN)"))
	}
	var raw []json.RawMessage
	body := http.MaxBytesReader(w, r.Body, maxBody)
	if err := json.NewDecoder(body).Decode(&raw); err != nil {
		var tooBig *http.MaxBytesError
		if errors.As(err, &tooBig) {
			return reply(w, http.StatusRequestEntityTooLarge, detail(fmt.Sprintf("Тело больше %d МБ — разбейте пачку", maxBody>>20)))
		}
		return reply(w, http.StatusUnprocessableEntity, detail("Ожидается JSON-массив событий"))
	}
	if len(raw) == 0 || len(raw) > maxEvents {
		return reply(w, http.StatusUnprocessableEntity, detail(fmt.Sprintf("В пачке должно быть от 1 до %d событий", maxEvents)))
	}
	ids := make([]*string, len(raw))
	payloads := make([]string, len(raw))
	for i, item := range raw {
		id, err := validate(item)
		if err != nil {
			return reply(w, http.StatusUnprocessableEntity, detail(fmt.Sprintf("events[%d]: %s", i, err)))
		}
		ids[i], payloads[i] = id, string(item)
	}
	added, err := s.store.Insert(r.Context(), ids, payloads)
	if err != nil {
		slog.Error("очередь недоступна", "err", err)
		return reply(w, http.StatusServiceUnavailable, detail("Очередь событий недоступна, повторите позже"))
	}
	s.metrics.events(added, len(raw)-added)
	return reply(w, http.StatusAccepted, accepted{Accepted: added, Duplicates: len(raw) - added})
}

func (s *server) health(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := s.store.Ping(ctx); err != nil {
		reply(w, http.StatusServiceUnavailable, map[string]string{"status": "db unavailable"})
		return
	}
	reply(w, http.StatusOK, map[string]string{"status": "ok"})
}

func detail(msg string) map[string]string { return map[string]string{"detail": msg} }

func reply(w http.ResponseWriter, code int, v any) int {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
	return code
}

func main() {
	healthcheck := flag.Bool("healthcheck", false, "проверить /health и выйти (для HEALTHCHECK в контейнере без shell)")
	flag.Parse()
	addr := env("INGEST_ADDR", ":8090")
	if *healthcheck {
		resp, err := http.Get("http://127.0.0.1" + addr + "/health")
		if err != nil || resp.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		return
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	token := os.Getenv("INGEST_TOKEN")
	if len(token) < 16 {
		slog.Error("INGEST_TOKEN должен быть не короче 16 символов")
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	pool, err := pgxpool.New(ctx, os.Getenv("DATABASE_URL"))
	if err != nil {
		slog.Error("DATABASE_URL", "err", err)
		os.Exit(1)
	}
	defer pool.Close()
	srv := &http.Server{
		Addr:              addr,
		Handler:           (&server{store: pgStore{pool}, token: token, metrics: newMetrics()}).routes(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      30 * time.Second,
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdown)
	}()
	slog.Info("сервис приёма событий запущен", "addr", addr)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("сервер", "err", err)
		os.Exit(1)
	}
	<-done // дождаться, пока начатые пачки ответят 202, — иначе биллинг повторит уже записанную
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
