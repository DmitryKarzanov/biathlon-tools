"""Конвертер протоколов биатлона в CSV/XLSX.

Принимает:
- PDF-файл (извлекается текст через pdfplumber)
- Или готовый текст (вставлен пользователем)

Извлекает данные через GigaChat (или другой AI-провайдер).
При ошибке AI — использует regex-фолбэк.
Возвращает CSV или XLSX.
"""

import io
import csv
import logging

import pdfplumber
from openpyxl import Workbook
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .services.ai_parser import extract_protocol

logger = logging.getLogger(__name__)


# ============================================================
# VIEWS
# ============================================================
def index(request):
    """Страница конвертера."""
    return render(request, 'pdf_converter/index.html')


@require_POST
def convert(request):
    """Обрабатывает PDF или текст, отдаёт CSV/XLSX."""
    pdf_file = request.FILES.get('pdf_file')
    text_in = (request.POST.get('protocol_text') or '').strip()
    fmt = (request.POST.get('format') or 'csv').lower()

    if fmt not in ('csv', 'xlsx'):
        return JsonResponse({'error': 'Неизвестный формат'}, status=400)

    # ---------- 1. Получаем текст ----------
    text = ''
    base_name = 'protocol'

    if pdf_file:
        if not pdf_file.name.lower().endswith('.pdf'):
            return JsonResponse({'error': 'Файл должен быть PDF'}, status=400)
        if pdf_file.size > 20 * 1024 * 1024:
            return JsonResponse({'error': 'Файл слишком большой (макс. 20 МБ)'}, status=400)
        try:
            with pdfplumber.open(pdf_file) as pdf:
                text = '\n'.join((p.extract_text() or '') for p in pdf.pages)
            base_name = pdf_file.name.rsplit('.', 1)[0] or 'protocol'
        except Exception as e:
            logger.exception('Ошибка чтения PDF')
            return JsonResponse({'error': f'Не удалось прочитать PDF: {e}'}, status=400)
    elif text_in:
        text = text_in
    else:
        return JsonResponse({'error': 'Загрузите PDF или вставьте текст'}, status=400)

    if len(text.strip()) < 30:
        return JsonResponse({'error': 'Слишком мало данных для обработки'}, status=400)

    # ---------- 2. Извлекаем данные (AI + regex-фолбэк) ----------
    try:
        data, source = extract_protocol(text)
    except Exception as e:
        logger.exception('Ошибка парсинга')
        return JsonResponse({'error': f'Ошибка обработки: {e}'}, status=500)

    athletes = data.get('athletes') or []
    tech = data.get('tech') or []
    race_meta = data.get('race_meta') or {}

    if not athletes:
        return JsonResponse(
            {'error': 'Не удалось найти данные спортсменов. '
                      'Проверьте, что скопирован или загружен корректный протокол.'},
            status=400,
        )

    # ---------- 3. Собираем файл ----------
    try:
        if fmt == 'csv':
            content = _build_csv(tech, race_meta, athletes)
            response = HttpResponse(content, content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="{base_name}.csv"'
        else:
            content = _build_xlsx(tech, race_meta, athletes)
            response = HttpResponse(
                content,
                content_type=(
                    'application/vnd.openxmlformats-officedocument.'
                    'spreadsheetml.sheet'
                ),
            )
            response['Content-Disposition'] = f'attachment; filename="{base_name}.xlsx"'

        # Сообщаем фронту, кто обработал запрос
        response['X-Parser-Source'] = source
        response['Access-Control-Expose-Headers'] = 'X-Parser-Source, Content-Disposition'
        return response

    except Exception as e:
        logger.exception('Ошибка сборки файла')
        return JsonResponse({'error': f'Не удалось собрать файл: {e}'}, status=500)


# ============================================================
# ЗАГОЛОВКИ И СТРОКИ ТАБЛИЦЫ
# ============================================================
def _build_athlete_header(race_meta: dict) -> list:
    """Строит заголовок таблицы с учётом числа рубежей."""
    shoot_cols = race_meta.get('shooting_columns') or ['Л', 'С']

    # 4 рубежа → Л1, С1, Л2, С2
    if len(shoot_cols) == 4:
        shoot_headers = [
            f'{shoot_cols[0]}1', f'{shoot_cols[1]}1',
            f'{shoot_cols[2]}2', f'{shoot_cols[3]}2',
        ]
    else:
        shoot_headers = list(shoot_cols)

    return (
        ['Место', 'Ст. №', 'Фамилия, Имя', 'РЕГ', 'Год',
         'Разряд', 'Чистое время']
        + shoot_headers
        + ['Сумма промахов', 'Время', 'Отставание', 'Очки',
           'Вып. разряд', 'Примечание']
    )


def _athlete_row(a: dict, race_meta: dict) -> list:
    """Строит строку спортсмена с учётом числа рубежей."""
    shoot_cols = race_meta.get('shooting_columns') or ['Л', 'С']
    shoot_count = len(shoot_cols)

    shooting = list(a.get('shooting') or [])
    while len(shooting) < shoot_count:
        shooting.append(0)
    shooting = shooting[:shoot_count]

    return (
        [
            a.get('place', ''),
            a.get('start_num', ''),
            a.get('name', ''),
            a.get('region', ''),
            a.get('year', ''),
            a.get('rank_qual', ''),
            a.get('clean_time', ''),
        ]
        + shooting
        + [
            a.get('shooting_sum', 0),
            a.get('time', ''),
            a.get('lost', ''),
            a.get('points', ''),
            a.get('rank_final', ''),
            a.get('note', ''),
        ]
    )


# ============================================================
# СБОРКА CSV
# ============================================================
def _build_csv(tech, race_meta, athletes) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')

    # Блок 1: техническая информация
    w.writerow(['Техническая информация'])
    for item in tech:
        w.writerow([item.get('key', ''), item.get('value', '')])

    w.writerow([])
    w.writerow(['Тип гонки', race_meta.get('type', '')])
    w.writerow(['Количество рубежей', race_meta.get('shooting_count', '')])
    w.writerow([])

    # Блок 2: спортсмены
    w.writerow(['Спортсмены'])
    w.writerow(_build_athlete_header(race_meta))
    for a in athletes:
        w.writerow(_athlete_row(a, race_meta))

    # BOM для корректного открытия в Excel
    return '\ufeff' + buf.getvalue()


# ============================================================
# СБОРКА XLSX
# ============================================================
def _build_xlsx(tech, race_meta, athletes) -> bytes:
    wb = Workbook()

    # Лист 1: техническая информация
    ws1 = wb.active
    ws1.title = 'Техническая информация'
    for item in tech:
        ws1.append([item.get('key', ''), item.get('value', '')])
    ws1.append([])
    ws1.append(['Тип гонки', race_meta.get('type', '')])
    ws1.append(['Количество рубежей', race_meta.get('shooting_count', '')])

    # Лист 2: спортсмены
    ws2 = wb.create_sheet('Спортсмены')
    ws2.append(_build_athlete_header(race_meta))
    for a in athletes:
        ws2.append(_athlete_row(a, race_meta))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
