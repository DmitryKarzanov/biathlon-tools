"""Извлечение данных протокола биатлона через нейросеть с фолбэком на regex.

Провайдеры:
- GigaChat (Sber) — основной, бесплатно до 365 млн токенов, без VPN.
- YandexGPT — альтернатива.
- Ollama — локальный запуск без интернета.

Переключение через AI_PROVIDER в settings.py.
"""

import json
import re
import logging
import uuid

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


# ============================================================
# ПРОМПТ
# ============================================================
SYSTEM_PROMPT = """Ты — парсер протоколов соревнований по биатлону.
На вход получаешь «сырой» текст протокола (результаты гонки).
Задача: извлечь ТОЛЬКО техническую информацию о гонке и список спортсменов.
Всё остальное (судьи, реквизиты, погода, логотипы, колонтитулы, номера страниц,
пустые строки, рекламные блоки) — игнорируй.

Верни строго JSON-объект такой структуры:

{
  "tech": [
    {"key": "Дата и время", "value": "..."},
    {"key": "Дисциплина", "value": "..."}
  ],
  "athletes": [
    {
      "place": 1,
      "start_num": 29,
      "name": "БУРДУКОВ Илья",
      "region": "МОС",
      "year": 2012,
      "clean_time": "18:37,2",
      "time": "19:07,2",
      "lost": "",
      "points": "",
    }
  ]
}

Правила:
- Поля place, start_num, year - числа.
- Время (clean_time, time, lost) — строка с запятой как разделителем: "18:37,2".
- Если поле отсутствует — оставь пустую строку "".
- Никаких пояснений, только JSON.
"""


# ============================================================
# БАЗОВЫЙ КЛАСС
# ============================================================
class BaseAIProvider:
    name = "base"

    def extract(self, text: str) -> dict:
        raise NotImplementedError


# ============================================================
# GIGACHAT
# ============================================================
class GigaChatProvider(BaseAIProvider):
    """
    GigaChat API от Сбера.
    Freemium для физлиц: 365 000 000 токенов.
    Данные на серверах в России. Работает без VPN.

    Ключ: https://developers.sber.ru/portal/products/gigachat-api
    """

    name = "gigachat"

    def __init__(self):
        self.auth_key = settings.GIGACHAT_AUTH_KEY
        if not self.auth_key:
            raise ValueError("GIGACHAT_AUTH_KEY не задан")
        self.base_url = "https://gigachat.devices.sberbank.ru/api/v1"
        self.oauth_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        self._access_token = None

    def _get_token(self) -> str:
        """Получает access token по Authorization key."""
        if self._access_token:
            return self._access_token

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {self.auth_key}",
        }
        data = {"scope": "GIGACHAT_API_PERS"}

        resp = requests.post(
            self.oauth_url,
            headers=headers,
            data=data,
            verify=False,  # сертификат Минцифры не всегда установлен
            timeout=30,
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]
        return self._access_token

    def extract(self, text: str) -> dict:
        token = self._get_token()
        text = text[:50000]

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }

        payload = {
            "model": "GigaChat",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.1,
            "max_tokens": 8000,
        }

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            verify=False,
            timeout=60,
        )
        resp.raise_for_status()

        raw = resp.json()["choices"][0]["message"]["content"]
        return _parse_json_response(raw)


# ============================================================
# YANDEXGPT
# ============================================================
class YandexGPTProvider(BaseAIProvider):
    name = "yandexgpt"

    def __init__(self):
        self.api_key = settings.YANDEX_API_KEY
        self.folder_id = settings.YANDEX_FOLDER_ID
        if not self.api_key or not self.folder_id:
            raise ValueError("YANDEX_API_KEY / YANDEX_FOLDER_ID не заданы")
        self.base_url = (
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        )

    def extract(self, text: str) -> dict:
        text = text[:50000]

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {self.api_key}",
        }
        payload = {
            "modelUri": f"gpt://{self.folder_id}/yandexgpt-lite/latest",
            "completionOptions": {
                "stream": False,
                "temperature": 0.1,
                "maxTokens": 8000,
            },
            "messages": [
                {"role": "system", "text": SYSTEM_PROMPT},
                {"role": "user", "text": text},
            ],
        }

        resp = requests.post(self.base_url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()

        raw = resp.json()["result"]["alternatives"][0]["message"]["text"]
        return _parse_json_response(raw)


# ============================================================
# OLLAMA
# ============================================================
class OllamaProvider(BaseAIProvider):
    name = "ollama"

    def __init__(self):
        self.base_url = getattr(settings, "OLLAMA_URL", "http://localhost:11434")
        self.model = getattr(settings, "OLLAMA_MODEL", "qwen2.5:7b")

    def extract(self, text: str) -> dict:
        text = text[:30000]

        payload = {
            "model": self.model,
            "prompt": f"{SYSTEM_PROMPT}\n\nТекст протокола:\n{text}",
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }

        resp = requests.post(
            f"{self.base_url}/api/generate", json=payload, timeout=120
        )
        resp.raise_for_status()

        raw = resp.json()["response"]
        return _parse_json_response(raw)


# ============================================================
# УТИЛИТЫ
# ============================================================
def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()

    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("В ответе модели нет JSON-объекта")

    data = json.loads(raw[start:end + 1])

    if not isinstance(data, dict):
        raise ValueError("Модель вернула не JSON-объект")
    if "athletes" not in data or not isinstance(data["athletes"], list):
        raise ValueError("В ответе нет списка спортсменов")

    return {
        "tech": data.get("tech") or [],
        "athletes": [_normalize_athlete(a) for a in data["athletes"]],
    }


def _normalize_athlete(a: dict) -> dict:
    def num(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def s(v):
        return (str(v) if v is not None else "").strip()

    def time_str(v):
        return s(v).replace(".", ",")

    return {
        "place":      num(a.get("place")),
        "start_num":  num(a.get("start_num")),
        "name":       s(a.get("name")),
        "region":     s(a.get("region")),
        "year":       num(a.get("year")),
        "clean_time": time_str(a.get("clean_time")),
        "rank_qual":  s(a.get("rank_qual")),
        "l1":         num(a.get("l1")),
        "l2":         num(a.get("l2")),
        "l3":         num(a.get("l3")),
        "sum":        num(a.get("sum")),
        "time":       time_str(a.get("time")),
        "lost":       time_str(a.get("lost")),
        "points":     s(a.get("points")),
        "rank_final": s(a.get("rank_final")),
        "note":       s(a.get("note")),
    }


# ============================================================
# ФОЛБЭК: REGEX
# ============================================================
REGION_CODES = [
    'МОС', 'МСК', 'МОБ', 'ЯРО', 'ВЛГ', 'ВЛА', 'ИВА', 'СПБ', 'ЛЕН', 'ННВ',
    'РЯЗ', 'ТАТ', 'САМ', 'УДМ', 'БАШ', 'КРА', 'НСК', 'ОМС', 'ТЮМ', 'КЕМ',
    'ХАН', 'ЯМА', 'ЧЕЛ', 'КУР', 'ОРЛ', 'ТУЛ', 'КАЛ', 'БРЯ', 'СМО', 'ПСК',
    'НОВ', 'ТВЕ', 'НИЖ', 'ПЕН', 'УЛЬ', 'САР', 'ВРН', 'БЕЛ', 'ЛИП', 'ТАМ',
    'КОС', 'АРХ', 'ВОЛ', 'МУР', 'КРЫ', 'АДЫ', 'СТА', 'РОС', 'АСТ', 'КБР',
    'КОМ', 'МАР', 'МОР', 'ХАК', 'АЛТ', 'ТЫВ', 'БУР', 'ЗАБ', 'ИРК', 'ОРЕ',
    'ПРИ', 'ХАБ', 'АМУ', 'ЕВР', 'МАГ', 'САХ', 'ЧУК', 'СЕВ', 'ЧЕЧ', 'ИНГ',
    'КАБ', 'КАР', 'ОСЕ', 'ДАГ', 'КЧР', 'КРД',
]
REGION_RE = r'(?:' + '|'.join(REGION_CODES) + r')'

ATHLETE_RE = re.compile(
    r'^(?P<place>\d{1,3})\s+(?P<start>\d{1,3})'
    r'\s+(?P<name>[А-ЯЁ][А-Яа-яёЁ\-]+(?:\s+[А-ЯЁ][А-Яа-яёЁ\-]+)+)'
    r'\s+(?P<region>' + REGION_RE + r')'
    r'\s+(?P<year>20\d{2})'
    r'\s+(?P<clean_time>\d{1,3}:\d{2}[,.]\d{1,2})'
    r'\s+(?P<rank_qual>(?:\d+\s*разряд|\d+\s*юр))'
    r'\s+(?P<l1>\d)\s+(?P<l2>\d)\s+(?P<l3>\d)\s+(?P<sum>\d{1,2})'
    r'\s+(?P<time>\d{1,3}:\d{2}[,.]\d{1,2})'
    r'(?:\s+(?P<lost>\+\d{1,3}:\d{2}[,.]\d{1,2}))?'
    r'(?:\s+(?P<rank_final>\d+\s*(?:разряд|юр)))?'
    r'(?:\s+(?P<note>.+?))?\s*$'
)


def _norm(line: str) -> str:
    for ch in ('\u00a0', '\u2009', '\u202f', '\t'):
        line = line.replace(ch, ' ')
    return re.sub(r'\s+', ' ', line).strip()


def regex_extract(text: str) -> dict:
    athletes = []
    for raw in text.split('\n'):
        line = _norm(raw)
        m = ATHLETE_RE.match(line)
        if not m:
            continue
        g = m.groupdict()
        athletes.append({
            'place':      int(g['place']),
            'start_num':  int(g['start']),
            'name':       g['name'].strip(),
            'region':     g['region'],
            'year':       int(g['year']),
            'clean_time': g['clean_time'].replace('.', ','),
            'rank_qual':  (g['rank_qual'] or '').strip(),
            'l1':         int(g['l1']),
            'l2':         int(g['l2']),
            'l3':         int(g['l3']),
            'sum':        int(g['sum']),
            'time':       g['time'].replace('.', ','),
            'lost':       (g['lost'] or '').replace('.', ','),
            'points':     '',
            'rank_final': (g['rank_final'] or '').strip(),
            'note':       (g['note'] or '').strip(),
        })

    tech = []
    head = [_norm(l) for l in text.split('\n')[:60]]
    for line in head:
        if re.search(r'(ГОНКА|СПРИНТ|ЭСТАФЕТА|ПАТРУЛЬ)', line, re.I) \
                and re.search(r'\d+\s*КМ', line, re.I):
            tech.append({'key': 'Дисциплина', 'value': line})
            break
    for line in head:
        m = re.search(
            r'([А-Я]{2}\s+\d{1,2}\s+[А-Я]{3,9}\s+\d{4}\s+\d{1,2}:\d{2})',
            line,
        )
        if m:
            tech.append({'key': 'Дата и время', 'value': m.group(1)})
            break
    for line in head:
        m = re.search(r'ЕКП\s*(\d+)', line)
        if m:
            tech.append({'key': 'ЕКП', 'value': m.group(1)})
            break

    return {'tech': tech, 'athletes': athletes}


# ============================================================
# ФАБРИКА
# ============================================================
PROVIDERS = {
    "gigachat": GigaChatProvider,
    "yandexgpt": YandexGPTProvider,
    "ollama": OllamaProvider,
}


def _get_provider():
    provider_name = getattr(settings, "AI_PROVIDER", "gigachat")
    cls = PROVIDERS.get(provider_name)
    if not cls:
        logger.error("Неизвестный AI_PROVIDER: %s", provider_name)
        return None
    try:
        return cls()
    except Exception as e:
        logger.warning("Не удалось инициализировать %s: %s", provider_name, e)
        return None


# ============================================================
# ТОЧКА ВХОДА
# ============================================================
def extract_protocol(text: str):
    provider = _get_provider()

    if provider:
        try:
            data = provider.extract(text)
            if data["athletes"]:
                return data, f"ai:{provider.name}"
            logger.warning("%s вернул 0 спортсменов, используем regex", provider.name)
        except Exception as e:
            logger.warning("AI-провайдер %s недоступен: %s", provider.name, e)

    data = regex_extract(text)
    return data, "regex"
