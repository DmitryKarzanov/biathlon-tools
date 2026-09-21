# check_gigachat.py
import uuid, requests

AUTH_KEY = "MDFhMGM1NzItNjQ5NS03NmJhLWIxMDctNTMxYjU3NmMxNTJhOmFmMjdiN2I1LWU4NTgtNGFiOC05NWZmLWYyMzBlYWQyN2M5OQ=="

# 1. Получаем access token
r = requests.post(
    "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
    headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {AUTH_KEY}",
    },
    data={"scope": "GIGACHAT_API_PERS"},
    verify=False,
    timeout=30,
)
print("OAuth status:", r.status_code)
print(r.text[:300])
token = r.json().get("access_token")
if not token:
    raise SystemExit("Не удалось получить токен")

# 2. Простой запрос
r2 = requests.post(
    "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
    headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    },
    json={
        "model": "GigaChat",
        "messages": [{"role": "user", "content": "Привет! Ответь одним словом."}],
        "temperature": 0.1,
        "max_tokens": 50,
    },
    verify=False,
    timeout=60,
)
print("Chat status:", r2.status_code)
print(r2.json()["choices"][0]["message"]["content"])