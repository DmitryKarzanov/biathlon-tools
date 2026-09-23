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
SYSTEM_PROMPT = """Ты — эксперт-парсер протоколов биатлонных соревнований.
На вход получаешь «сырой» текст протокола (результаты гонки, часто криво извлечённые из PDF).
Задача: извлечь ТОЛЬКО данные о спортсменах и минимальную техническую информацию о гонке.
Всё остальное (судьи, техделегаты, погода, реквизиты, логотипы, колонтитулы,
номера страниц, реклама, организаторы, спонсоры) — ИГНОРИРУЙ.

Верни строго JSON-объект такой структуры:

{
  "tech": [
    {"key": "Соревнование",    "value": "ПЕРВЕНСТВО РОССИИ ПО БИАТЛОНУ"},
    {"key": "Место проведения", "value": "Г. САРАНСК (РЕСПУБЛИКА МОРДОВИЯ)"},
    {"key": "Дата и время",    "value": "ЧТ 17 СЕН 2026 14:30"},
    {"key": "ЕКП",             "value": "2040130022045756"},
    {"key": "Дисциплина",      "value": "РОЛЛЕРЫ-ГОНКА 7,5 КМ ДЕВУШКИ 15-16 ЛЕТ"}
  ],
  "race_meta": {
    "type": "individual",
    "shooting_count": 4,
    "shooting_columns": ["Л", "С", "Л", "С"]
  },
  "athletes": [
    {
      "place": 1,
      "start_num": 131,
      "name": "АЛЕКСЕЕВ Кирилл Иванович",
      "region": "ТЮМ",
      "year": 2011,
      "rank_qual": "2 разряд",
      "clean_time": "25:45.9",
      "shooting": [0, 0, 0, 1],
      "shooting_sum": 1,
      "time": "26:30.9",
      "lost": "",
      "points": "",
      "rank_final": "КМС",
      "note": ""
    }
  ]
}

=== ОПРЕДЕЛЕНИЕ ТИПА ГОНКИ (race_meta) ===
Смотри заголовок протокола (первые 30 строк):
- "individual" — если есть «ГОНКА», «ИНДИВИДУАЛЬНАЯ», «РОЛЛЕРЫ-ГОНКА».
  Количество рубежей = 4 (2 лежа + 2 стоя). shooting_columns = ["Л","С","Л","С"].
- "sprint" — если есть «СПРИНТ». 2 рубежа. shooting_columns = ["Л","С"].
- "mass_start" — если «МАСС-СТАРТ». 2 рубежа. shooting_columns = ["Л","С"].
- "pursuit" — если «ПРЕСЛЕДОВАНИЕ» или «ПАРСУТ». 4 рубежа. shooting_columns = ["Л","С","Л","С"].
- "relay" — если «ЭСТАФЕТА». 2 рубежа.
- Если тип неясен — type="other", shooting_count=2, shooting_columns=["Л","С"].

=== ПРАВИЛА ПО СПОРТСМЕНАМ ===

Обязательные поля (всегда, для каждого спортсмена):
- place      (int)   — место в протоколе
- start_num  (int)   — стартовый номер
- name       (str)   — ФИО как в протоколе (2 или 3 слова)
- year       (int)   — только год рождения (например, 2011)
- time       (str)   — итоговое время финиша, формат "MM:SS.D" или "M:SS.D" или "H:MM:SS.D"
- shooting   (list[int]) — промахи по рубежам, столько значений, сколько столбцов (2 или 4)
- shooting_sum (int) — суммарные промахи

Необязательные поля (если отсутствуют — пустая строка ""):
- region     — код региона: 2-4 заглавные буквы (МОС, ТЮМ, БАШ, СПБ, ЧЕЛ, НВС, ...)
- rank_qual  — разряд из столбца «Разряд» (КМС, МС, 1 разряд, 2 разряд, 3 разряд, 1 юр)
- clean_time — «чистое время» (только для индивидуальных гонок)
- lost       — отставание от лидера, со знаком "+": "+1:29.2" (у лидера пусто)
- points     — очки (из столбца «Очки Рег.» или «Очки»)
- rank_final — выполненный разряд (из столбца «Вып. разряд»)
- note       — организация спортсмена (то, что идёт ОТДЕЛЬНОЙ строкой после основной:
  "Республика Башкортостан, г. Уфа, ГБУ ДО СШОР по биатлону РБ")

=== ЧТО НЕ ВКЛЮЧАТЬ В athletes ===
НИКОГДА:
- судей, техделегатов, главного судью, главного секретаря (они в шапке и в конце);
- участников из разделов «Не стартовали», «Не финишировали», «Дисквалифицированы»;
- строки из «Решения жюри»;
- пустые строки, заголовки, подписи, рекламу.

=== ОСОБЫЕ СЛУЧАИ ===
1. Дублирующиеся номера в строке (OCR-склейка). Пример сырого текста:
   "1 131131АЛЕКСЕЕВ Кирилл Иванович" — это НЕ два номера 131, а place=1, start_num=131.
   Нормальный вид: "1 131 АЛЕКСЕЕВ Кирилл Иванович".
2. ФИО из 2 слов ("САХРАН Виктория") или 3 слов ("АЛЕКСЕЕВ Кирилл Иванович") — сохраняй как есть.
3. Повторяющиеся ФИО вроде "ПАСКОВА СОФИЯ, ПАСКОВА СОФИЯ" — оставь одну запись.
4. Если РЕГ отсутствует в строке — оставь region = "".
5. Если столбцы стрельбы помечены как «Л С Л С» — значит 4 рубежа, shooting = [l1, s1, l2, s2].
   Если «Л С» — 2 рубежа, shooting = [l1, s1].
6. Время сохраняй с запятой: "18:37,2" (не "18:37.2"). Это важно для Excel.
7. Если у спортсмена в отставании стоит "0.0" или пусто — оставь lost = "".

=== ТЕХНИЧЕСКАЯ ИНФОРМАЦИЯ (tech) ===
Ищи в первых 40 строках текста:
- Соревнование — общее название турнира
- Место проведения — город, стадион
- Дата и время — полная дата и время старта гонки
- ЕКП — числовой код (только цифры)
- Дисциплина — строка с типом гонки, дистанцией и категорией

=== ФОРМАТ ОТВЕТА ===
Только валидный JSON-объект. Никаких пояснений, комментариев, markdown-обёрток.
Если поле отсутствует — не выдумывай: "" для строк, 0 для чисел, [] для списков.
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

    race_meta = data.get("race_meta") or {}
    if not race_meta.get("shooting_columns"):
        race_meta["shooting_columns"] = ["Л", "С"]

    return {
        "tech":      data.get("tech") or [],
        "race_meta": race_meta,
        "athletes":  [_normalize_athlete(a) for a in data["athletes"]],
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

    # Стрельба: поддерживаем и список, и старые поля l1..l3
    if isinstance(a.get("shooting"), list):
        shooting = [num(x) for x in a["shooting"]]
    else:
        shooting = [num(a.get(f"l{i}")) for i in range(1, 5)]
        while len(shooting) > 2 and shooting[-1] == 0:
            shooting.pop()

    sum_val = num(a.get("shooting_sum") or a.get("sum"), sum(shooting))

    return {
        "place":        num(a.get("place")),
        "start_num":    num(a.get("start_num") or a.get("start")),
        "name":         s(a.get("name")),
        "region":       s(a.get("region")),
        "year":         num(a.get("year")),
        "rank_qual":    s(a.get("rank_qual")),
        "clean_time":   time_str(a.get("clean_time")),
        "shooting":     shooting,
        "shooting_sum": sum_val,
        "time":         time_str(a.get("time")),
        "lost":         time_str(a.get("lost")),
        "points":       s(a.get("points")),
        "rank_final":   s(a.get("rank_final")),
        "note":         s(a.get("note")),
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
    'КАБ', 'КАР', 'ОСЕ', 'ДАГ', 'КЧР', 'КРД', 'ЧУВ', 'КИР', 'ТАМ', 'СВЕ',
    'ЯКУ', 'КАМ', 'КГА', 'САХ', 'МУР', 'ТЫВ', 'ХАК', 'АЛТ', 'ОМС', 'ТОМ',
]
REGION_RE = r'(?:' + '|'.join(set(REGION_CODES)) + r')'

# 2 рубежа: Л С Сум
ATHLETE_RE_2 = re.compile(
    r'^(?P<place>\d{1,3})\s+(?P<start>\d{1,3})'
    r'\s+(?P<name>[А-ЯЁ][А-Яа-яёЁ\-]+(?:\s+[А-ЯЁ][А-Яа-яёЁ\-]+){1,2})'
    r'(?:\s+(?P<region>' + REGION_RE + r'))?'
    r'\s+(?P<date>\d{1,2}\.\d{1,2}\.\d{4})'
    r'\s+(?P<rank_qual>(?:КМС|МС|1\s*разряд|2\s*разряд|3\s*разряд|1\s*юр|2\s*юр|3\s*юр))'
    r'\s+(?P<l1>\d)\s+(?P<s1>\d)\s+(?P<sum>\d{1,2})'
    r'\s+(?P<time>\d{1,3}:\d{2}[,.]\d{1,2})'
    r'(?:\s+(?P<lost>\+\d{1,3}:\d{2}[,.]\d{1,2}))?'
    r'(?:\s+(?P<points>\d+))?'
    r'(?:\s+(?P<rank_final>(?:КМС|МС|1\s*разряд|2\s*разряд|3\s*разряд|1\s*юр)))?'
    r'\s*$'
)

# 4 рубежа: Л С Л С Сум
ATHLETE_RE_4 = re.compile(
    r'^(?P<place>\d{1,3})\s+(?P<start>\d{1,3})'
    r'\s+(?P<name>[А-ЯЁ][А-Яа-яёЁ\-]+(?:\s+[А-ЯЁ][А-Яа-яёЁ\-]+){1,2})'
    r'(?:\s+(?P<region>' + REGION_RE + r'))?'
    r'\s+(?P<date>\d{1,2}\.\d{1,2}\.\d{4})'
    r'\s+(?P<rank_qual>(?:КМС|МС|1\s*разряд|2\s*разряд|3\s*разряд|1\s*юр))'
    r'\s+(?P<clean_time>\d{1,3}:\d{2}[,.]\d{1,2})'
    r'\s+(?P<l1>\d)\s+(?P<s1>\d)\s+(?P<l2>\d)\s+(?P<s2>\d)\s+(?P<sum>\d{1,2})'
    r'\s+(?P<time>\d{1,3}:\d{2}[,.]\d{1,2})'
    r'(?:\s+(?P<lost>\+\d{1,3}:\d{2}[,.]\d{1,2}))?'
    r'\s*$'
)


def _norm(line: str) -> str:
    for ch in ('\u00a0', '\u2009', '\u202f', '\t'):
        line = line.replace(ch, ' ')
    return re.sub(r'\s+', ' ', line).strip()


def _detect_race_type(text: str) -> dict:
    """Определяет тип гонки по заголовку протокола."""
    head = "\n".join(text.split("\n")[:40]).upper()

    if re.search(r'СПРИНТ', head):
        return {"type": "sprint", "shooting_count": 2, "shooting_columns": ["Л", "С"]}
    if re.search(r'МАСС-?СТАРТ', head):
        return {"type": "mass_start", "shooting_count": 2, "shooting_columns": ["Л", "С"]}
    if re.search(r'ПРЕСЛЕДОВАНИЕ|ПАРСУТ', head):
        return {"type": "pursuit", "shooting_count": 4, "shooting_columns": ["Л", "С", "Л", "С"]}
    if re.search(r'ЭСТАФЕТ', head):
        return {"type": "relay", "shooting_count": 2, "shooting_columns": ["Л", "С"]}
    if re.search(r'ГОНКА|ИНДИВИДУАЛЬН', head):
        return {"type": "individual", "shooting_count": 4, "shooting_columns": ["Л", "С", "Л", "С"]}
    return {"type": "other", "shooting_count": 2, "shooting_columns": ["Л", "С"]}


def regex_extract(text: str) -> dict:
    race_meta = _detect_race_type(text)
    use_4 = race_meta["shooting_count"] == 4
    pattern = ATHLETE_RE_4 if use_4 else ATHLETE_RE_2

    athletes = []
    for raw in text.split('\n'):
        line = _norm(raw)
        m = pattern.match(line)
        if not m:
            continue
        g = m.groupdict()

        if use_4:
            shooting = [int(g['l1']), int(g['s1']), int(g['l2']), int(g['s2'])]
        else:
            shooting = [int(g['l1']), int(g['s1'])]

        athletes.append({
            'place':        int(g['place']),
            'start_num':    int(g['start']),
            'name':         g['name'].strip(),
            'region':       (g.get('region') or '').strip(),
            'year':         int(g['date'].split('.')[-1]),
            'rank_qual':    (g.get('rank_qual') or '').strip(),
            'clean_time':   (g.get('clean_time') or '').replace('.', ','),
            'shooting':     shooting,
            'shooting_sum': int(g['sum']),
            'time':         g['time'].replace('.', ','),
            'lost':         (g.get('lost') or '').replace('.', ','),
            'points':       (g.get('points') or '').strip(),
            'rank_final':   (g.get('rank_final') or '').strip(),
            'note':         '',
        })

    tech = []
    head = [_norm(l) for l in text.split('\n')[:60]]
    for line in head:
        if re.search(r'(ГОНКА|СПРИНТ|ЭСТАФЕТА|РОЛЛЕРЫ)', line, re.I) and re.search(r'\d+[,.]?\d*\s*КМ', line, re.I):
            tech.append({'key': 'Дисциплина', 'value': line})
            break
    for line in head:
        m = re.search(r'([А-Я]{2}\s+\d{1,2}\s+[А-Я]{3,9}\s+\d{4}\s+\d{1,2}:\d{2})', line)
        if m:
            tech.append({'key': 'Дата и время', 'value': m.group(1)})
            break
    for line in head:
        m = re.search(r'ЕКП\s*(\d+)', line)
        if m:
            tech.append({'key': 'ЕКП', 'value': m.group(1)})
            break
    for line in head:
        if re.search(r'ПЕРВЕНСТВ|ЧЕМПИОНАТ|КУБОК|СОРЕВНОВАНИ', line, re.I):
            tech.append({'key': 'Соревнование', 'value': line})
            break
    for line in head:
        if re.search(r'ОБЛАСТЬ|КРАЙ|РЕСПУБЛИКА', line) and re.search(r'[ГД]\.\s+[А-Я]', line):
            tech.append({'key': 'Место проведения', 'value': line})
            break

    return {'tech': tech, 'race_meta': race_meta, 'athletes': athletes}


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
