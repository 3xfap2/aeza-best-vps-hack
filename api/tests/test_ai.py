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


def test_config_from_env():
    assert ai.config_from_env({}) is None
    assert ai.config_from_env({"AI_PROVIDER": "off"}) is None
    cfg = ai.config_from_env({"AI_PROVIDER": "yandex", "AI_API_KEY": "k", "AI_FOLDER_ID": "b1g"})
    assert cfg.model == "yandexgpt/latest" and cfg.folder_id == "b1g"
    with pytest.raises(RuntimeError):
        ai.config_from_env({"AI_PROVIDER": "yandex", "AI_API_KEY": "k"})
    assert ai.config_from_env({"AI_PROVIDER": "gigachat", "AI_API_KEY": "k"}).model == "GigaChat-2"
