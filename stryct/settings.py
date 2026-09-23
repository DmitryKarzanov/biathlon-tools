"""
Django settings для проекта stryct (biathlon-tools).

Локально:  DEBUG=True, работает по http://127.0.0.1:8000
На RelaxDev: DEBUG=False, работает за HTTPS-прокси.
"""

from pathlib import Path
import os

from dotenv import load_dotenv


# ============================================================
# БАЗОВЫЕ ПУТИ
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent

# Загружаем переменные из .env (ищем в двух местах)
load_dotenv(BASE_DIR.parent / '.env')
load_dotenv(BASE_DIR / '.env')


# ============================================================
# БЕЗОПАСНОСТЬ
# ============================================================
SECRET_KEY = os.environ.get(
    'SECRET_KEY',
    'django-insecure-dev-only-key-do-not-use-in-production',
)

DEBUG = os.environ.get('DEBUG', 'True').lower() in ('1', 'true', 'yes', 'on')

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get(
        'ALLOWED_HOSTS',
        '127.0.0.1,localhost,biathlon-tools.relaxdev.ru'
    ).split(',')
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        'CSRF_TRUSTED_ORIGINS',
        'https://biathlon-tools.relaxdev.ru'
    ).split(',')
    if o.strip()
]


# ============================================================
# AI ПРОВАЙДЕР
# ============================================================
AI_PROVIDER = os.environ.get('AI_PROVIDER', 'gigachat')

# GigaChat (Сбер) — основной провайдер
GIGACHAT_AUTH_KEY = os.environ.get('GIGACHAT_AUTH_KEY', '')

# YandexGPT (альтернатива)
YANDEX_API_KEY = os.environ.get('YANDEX_API_KEY', '')
YANDEX_FOLDER_ID = os.environ.get('YANDEX_FOLDER_ID', '')

# Ollama (локально)
OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'qwen2.5:7b')


# ============================================================
# ПРИЛОЖЕНИЯ
# ============================================================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Локальные приложения
    'homepage',
    'leader_track',
    'pdf_converter',
]


# ============================================================
# MIDDLEWARE
# ============================================================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


# ============================================================
# URL / WSGI
# ============================================================
ROOT_URLCONF = 'stryct.urls'
WSGI_APPLICATION = 'stryct.wsgi.application'


# ============================================================
# ШАБЛОНЫ
# ============================================================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]


# ============================================================
# БАЗА ДАННЫХ
# ============================================================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# ============================================================
# ПАРОЛИ
# ============================================================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ============================================================
# ЯЗЫК И ВРЕМЯ
# ============================================================
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True


# ============================================================
# СТАТИКА
# ============================================================
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STATICFILES_DIRS = []
_static_dir = BASE_DIR / 'static'
if _static_dir.exists():
    STATICFILES_DIRS.append(_static_dir)

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}


# ============================================================
# ЗАГРУЗКА ФАЙЛОВ
# ============================================================
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024


# ============================================================
# ПРОЧЕЕ
# ============================================================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}


# ============================================================
# ПРОДАКШЕН-НАСТРОЙКИ (только при DEBUG=False)
# ============================================================
if not DEBUG:
    # HTTPS за прокси RelaxDev
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True

    # Редирект на HTTPS
    SECURE_SSL_REDIRECT = True

    # Cookies только по HTTPS
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # HSTS
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    # Прочие заголовки
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'
    X_FRAME_OPTIONS = 'DENY'
