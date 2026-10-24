import pytest

from app import ai


def test_numbers_are_checked_against_facts():
    facts = "Ожидаемая потеря: € 8655.98; клиент #62: удалится 20.10 11:20, сохраним € 16.26"
    assert ai.unverified_numbers("Под угрозой € 8 656, клиент #62 удалится 20.10", facts) == []
    assert ai.unverified_numbers("Сохраним € 16,26 у клиента #62", facts) == []
    assert ai.unverified_numbers("Потеря вырастет до € 12 000", facts) == ["12 000"]
    assert ai.unverified_numbers("Позвоните 3 клиентам", facts) == []   # мелкие счётчики не проверяем


def test_emails_never_reach_the_model():
    emails = {"user-0062@example.test": 62}
    text = ai.scrub("Клиент user-0062@example.test и кто-то other@mail.ru", emails)
    assert "@" not in text and "клиент #62" in text and "[email]" in text
    assert ai.mentioned_customers("почему клиент #62 и user-0005?", emails, {5, 62}) == [62, 5]
    assert ai.mentioned_customers("что с user-0062@example.test", emails, {62}) == [62]


class Fake:
    title = "Fake"
    cfg = ai.AIConfig(provider="yandex", api_key="k", model="m", folder_id="f")

    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, max_tokens=700, temperature=0.2):
        assert "@example.test" not in user          # персональные данные не уходят в модель
        return self.reply(user) if callable(self.reply) else self.reply


def test_message_rejects_invented_numbers():
    facts = "Баланс € 1.42, нужно на продления € 90.76"
    good = ai.message(Fake("Тема: Сервер ждёт оплаты\n\nПополните счёт на € 90,76."), facts, "Ждёт оплаты")
    assert good["subject"] == "Сервер ждёт оплаты" and "90,76" in good["text"]
    with pytest.raises(ai.AIError):
        ai.message(Fake("Тема: Скидка\n\nДарим скидку 25 % и € 500 на счёт."), facts, "Ждёт оплаты")


def test_cbr_rate_is_parsed_and_cached():
    import httpx
    from app import fx
    xml = ('<?xml version="1.0" encoding="windows-1251"?><ValCurs Date="24.10.2026" name="Foreign Currency Market">'
           '<Valute ID="R01239"><NumCode>978</NumCode><CharCode>EUR</CharCode><Nominal>1</Nominal><Name>Евро</Name>'
           '<Value>90,2429</Value></Valute></ValCurs>').encode("cp1251")
    fx._cache = None
    calls = []
    t = httpx.MockTransport(lambda r: calls.append(1) or httpx.Response(200, content=xml))
    out = fx.eur_rub(t)
    assert out == {"rate": 90.2429, "date": "2026-10-24", "source": "ЦБ РФ", "stale": False}
    assert fx.eur_rub(t)["rate"] == 90.2429 and len(calls) == 1            # второй раз — из кеша
    fx._cache["fetched"] = 0
    down = httpx.MockTransport(lambda r: httpx.Response(503))
    assert fx.eur_rub(down)["stale"] is True                               # ЦБ недоступен — последний известный
    fx._cache = None


def test_config_from_env():
    assert ai.config_from_env({}) is None
    assert ai.config_from_env({"AI_PROVIDER": "off"}) is None
    cfg = ai.config_from_env({"AI_PROVIDER": "yandex", "AI_API_KEY": "k", "AI_FOLDER_ID": "b1g"})
    assert cfg.model == "yandexgpt/latest" and cfg.folder_id == "b1g"
    with pytest.raises(RuntimeError):
        ai.config_from_env({"AI_PROVIDER": "yandex", "AI_API_KEY": "k"})
    assert ai.config_from_env({"AI_PROVIDER": "gigachat", "AI_API_KEY": "k"}).model == "GigaChat-2"
    assert ai.config_from_env({"AI_PROVIDER": "openai", "AI_API_KEY": "sk-x"}).base_url == "https://api.openai.com/v1"


def test_openai_requests_and_fallbacks():
    import httpx
    import json
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        assert request.headers["authorization"] == "Bearer sk-test" and request.url.path == "/v1/chat/completions"
        if "temperature" in body and body["model"] == "strict-model":
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'temperature'"}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": " Ответ "}}]})

    def client(model):
        return ai.AIClient(ai.AIConfig(provider="openai", api_key="sk-test", model=model), transport=httpx.MockTransport(handler))

    assert client("gpt-4.1-mini").complete("s", "u") == "Ответ"
    assert sent[-1]["temperature"] == 0.2 and sent[-1]["max_completion_tokens"] == 700
    assert client("gpt-5-mini").complete("s", "u") == "Ответ"
    assert "temperature" not in sent[-1] and sent[-1]["reasoning_effort"] == "low"
    strict = client("strict-model")
    assert strict.complete("s", "u") == "Ответ" and "temperature" not in sent[-1]      # повтор без temperature
    region = ai.AIClient(ai.AIConfig(provider="openai", api_key="sk-test", model="m"), transport=httpx.MockTransport(
        lambda r: httpx.Response(403, json={"error": {"code": "unsupported_country_region_territory"}})))
    with pytest.raises(ai.AIError, match="AI_PROXY"):
        region.complete("s", "u")


def test_aeza_profile_and_auto_role():
    import httpx
    from app import aeza_auth
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {"total": 1, "items": [
        {"id": 4242, "email": "Anna@Aeza.net", "name": "Анна"}]}}) if r.headers["x-api-key"] == "good-key-123" else
        httpx.Response(401, json={"error": {"slug": "unauthorized", "message": "Invalid API key"}}))
    p = aeza_auth.fetch_profile("good-key-123", ok)
    assert p == {"id": "4242", "email": "anna@aeza.net", "name": "Анна"}
    with pytest.raises(aeza_auth.AezaAuthError, match="не подошёл"):
        aeza_auth.fetch_profile("bad-key-0000", ok)
    assert aeza_auth.auto_role("anna@aeza.net", "@aeza.net") == "marketer"
    assert aeza_auth.auto_role("boss@corp.ru", "@aeza.net, boss@corp.ru") == "marketer"
    assert aeza_auth.auto_role("stranger@mail.ru", "@aeza.net") == "pending"
