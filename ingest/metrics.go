package main

import (
	"fmt"
	"net/http"
	"sort"
	"sync"
	"time"
)

// metrics — счётчики в текстовом формате Prometheus (без сторонних библиотек).
type metrics struct {
	mu         sync.Mutex
	requests   map[int]int64
	accepted   int64
	duplicates int64
	seconds    float64
	count      int64
}

func newMetrics() *metrics { return &metrics{requests: map[int]int64{}} }

func (m *metrics) request(code int, took time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.requests[code]++
	m.seconds += took.Seconds()
	m.count++
}

func (m *metrics) events(accepted, duplicates int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.accepted += int64(accepted)
	m.duplicates += int64(duplicates)
}

func (m *metrics) serve(w http.ResponseWriter, _ *http.Request) {
	m.mu.Lock()
	defer m.mu.Unlock()
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	fmt.Fprintln(w, "# HELP radar_ingest_requests_total Запросы на приём событий по коду ответа")
	fmt.Fprintln(w, "# TYPE radar_ingest_requests_total counter")
	codes := make([]int, 0, len(m.requests))
	for c := range m.requests {
		codes = append(codes, c)
	}
	sort.Ints(codes)
	for _, c := range codes {
		fmt.Fprintf(w, "radar_ingest_requests_total{code=\"%d\"} %d\n", c, m.requests[c])
	}
	fmt.Fprintln(w, "# HELP radar_ingest_events_total Событий принято в очередь")
	fmt.Fprintln(w, "# TYPE radar_ingest_events_total counter")
	fmt.Fprintf(w, "radar_ingest_events_total %d\n", m.accepted)
	fmt.Fprintln(w, "# HELP radar_ingest_duplicates_total Повторно доставленных событий (тот же eventId), отброшено")
	fmt.Fprintln(w, "# TYPE radar_ingest_duplicates_total counter")
	fmt.Fprintf(w, "radar_ingest_duplicates_total %d\n", m.duplicates)
	fmt.Fprintln(w, "# HELP radar_ingest_request_seconds Время обработки пачки")
	fmt.Fprintln(w, "# TYPE radar_ingest_request_seconds summary")
	fmt.Fprintf(w, "radar_ingest_request_seconds_sum %g\n", m.seconds)
	fmt.Fprintf(w, "radar_ingest_request_seconds_count %d\n", m.count)
}
