package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type fakeStore struct {
	ids  map[string]bool
	fail bool
}

func (f *fakeStore) Insert(_ context.Context, ids []*string, _ []string) (int, error) {
	if f.fail {
		return 0, errors.New("down")
	}
	added := 0
	for _, id := range ids {
		if id != nil && f.ids[*id] {
			continue
		}
		if id != nil {
			f.ids[*id] = true
		}
		added++
	}
	return added, nil
}

func (f *fakeStore) Ping(context.Context) error { return nil }

const token = "test-ingest-token-2026"

func post(t *testing.T, h http.Handler, auth, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/api/v1/ingest/events", strings.NewReader(body))
	if auth != "" {
		req.Header.Set("Authorization", auth)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func TestEvents(t *testing.T) {
	store := &fakeStore{ids: map[string]bool{}}
	h := (&server{store: store, token: token, metrics: newMetrics()}).routes()
	batch := `[{"eventId":"a1","type":"topup","accountId":3,"amount":10.5},
		{"eventId":"a2","type":"pay_renewal","serviceId":7},
		{"type":"signal","name":"loadDrop","targetId":7,"value":0.8}]`

	cases := []struct {
		name, auth, body string
		code             int
		want             string
	}{
		{"без токена", "", batch, 401, "INGEST_TOKEN"},
		{"чужой токен", "Bearer wrong-token-wrong-token", batch, 401, "INGEST_TOKEN"},
		{"пачка принята", "Bearer " + token, batch, 202, `"accepted":3,"duplicates":0`},
		{"повтор тех же eventId", "Bearer " + token, batch, 202, `"accepted":1,"duplicates":2`},
		{"не массив", "Bearer " + token, `{"type":"topup"}`, 422, "JSON-массив"},
		{"пустая пачка", "Bearer " + token, `[]`, 422, "от 1 до"},
		{"неизвестный тип", "Bearer " + token, `[{"type":"x"}]`, 422, "events[0]: неизвестный type"},
		{"дробный id", "Bearer " + token, `[{"type":"pay_renewal","serviceId":1.5}]`, 422, "serviceId"},
		{"сумма вне диапазона", "Bearer " + token, `[{"type":"topup","accountId":1,"amount":0}]`, 422, "amount"},
		{"плохой статус", "Bearer " + token, `[{"type":"service_status","serviceId":1,"status":"gone"}]`, 422, "status"},
	}
	for _, c := range cases {
		rec := post(t, h, c.auth, c.body)
		if rec.Code != c.code || !strings.Contains(rec.Body.String(), c.want) {
			t.Errorf("%s: код %d, тело %s; ждали %d и %q", c.name, rec.Code, rec.Body.String(), c.code, c.want)
		}
	}

	big := "[" + strings.Repeat(`{"type":"pay_renewal","serviceId":1},`, maxEvents) + `{"type":"pay_renewal","serviceId":1}]`
	if rec := post(t, h, "Bearer "+token, big); rec.Code != 422 {
		t.Errorf("пачка больше %d: код %d", maxEvents, rec.Code)
	}
	store.fail = true
	if rec := post(t, h, "Bearer "+token, batch); rec.Code != 503 {
		t.Errorf("очередь недоступна: код %d", rec.Code)
	}

	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	for _, want := range []string{`radar_ingest_requests_total{code="202"} 2`, "radar_ingest_events_total 4", "radar_ingest_duplicates_total 2"} {
		if !strings.Contains(rec.Body.String(), want) {
			t.Errorf("в /metrics нет %q:\n%s", want, rec.Body.String())
		}
	}
}
