package main

import (
	"encoding/json"
	"errors"
	"fmt"
)

// Форма событий — как у POST /api/v1/source/events в API. Здесь — быстрая проверка формы, чтобы
// биллинг сразу получил 422 с номером события; полную проверку (есть ли такой клиент) делает API.
var (
	statuses = set("active", "suspended", "prolong_wait", "deleted", "blocked")
	signals  = set("supportTickets30d", "loadDrop", "downgraded")
)

func set(v ...string) map[string]bool {
	m := make(map[string]bool, len(v))
	for _, s := range v {
		m[s] = true
	}
	return m
}

// validate проверяет событие и возвращает его eventId (ключ от повторной доставки), если он есть.
func validate(raw json.RawMessage) (*string, error) {
	var e map[string]any
	if err := json.Unmarshal(raw, &e); err != nil || e == nil {
		return nil, errors.New("событие должно быть JSON-объектом")
	}
	var id *string
	if v, ok := e["eventId"]; ok {
		s, isStr := v.(string)
		if !isStr || s == "" || len(s) > 200 {
			return nil, errors.New("eventId — непустая строка до 200 символов")
		}
		id = &s
	}
	kind, _ := e["type"].(string)
	switch kind {
	case "topup":
		if err := positiveInt(e, "accountId"); err != nil {
			return nil, err
		}
		amount, ok := e["amount"].(float64)
		if !ok || amount < 0.01 || amount > 100000 {
			return nil, errors.New("amount — сумма пополнения в евро, от 0,01 до 100 000")
		}
	case "autoprolong":
		if err := positiveInt(e, "serviceId"); err != nil {
			return nil, err
		}
		if _, ok := e["enabled"].(bool); !ok {
			return nil, errors.New("enabled — true или false")
		}
	case "service_status":
		if err := positiveInt(e, "serviceId"); err != nil {
			return nil, err
		}
		if s, _ := e["status"].(string); !statuses[s] {
			return nil, errors.New("status — active, suspended, prolong_wait, deleted или blocked")
		}
	case "pay_renewal":
		if err := positiveInt(e, "serviceId"); err != nil {
			return nil, err
		}
	case "signal":
		if s, _ := e["name"].(string); !signals[s] {
			return nil, errors.New("name — supportTickets30d, loadDrop или downgraded")
		}
		if err := positiveInt(e, "targetId"); err != nil {
			return nil, err
		}
		switch e["value"].(type) {
		case float64, bool:
		default:
			return nil, errors.New("value — число или true/false")
		}
	default:
		return nil, fmt.Errorf("неизвестный type %q: topup, autoprolong, service_status, pay_renewal, signal", kind)
	}
	return id, nil
}

func positiveInt(e map[string]any, key string) error {
	v, ok := e[key].(float64)
	if !ok || v < 1 || v != float64(int64(v)) {
		return fmt.Errorf("%s — целое число от 1", key)
	}
	return nil
}
