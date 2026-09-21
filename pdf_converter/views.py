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


ATHLETE_HEADER = [
    'Место', 'Ст. №', 'Фамилия, Имя', 'РЕГ', 'Год',
    'Чистое время', 'Разряд',
    'Л1', 'Л2', 'Л3', 'Сумма',
    'Время', 'Отставание', 'Очки', 'Вып. разряд', 'Примечание',
]


def index(request):
    return render(request, 'pdf_converter/index.html')


@require_POST
def convert(request):
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

    if not athletes:
        return JsonResponse(
            {'error': 'Не удалось найти данные спортсменов. '
                      'Проверьте, что скопирован или загружен корректный протокол.'},
            status=400,
        )

    # ---------- 3. Собираем файл ----------
    if fmt == 'csv':
        content = _build_csv(tech, athletes)
        response = HttpResponse(content, content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{base_name}.csv"'
    else:
        content = _build_xlsx(tech, athletes)
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


# ============================================================
# СБОРКА ФАЙЛОВ
# ============================================================
def _athlete_row(a):
    return [
        a.get('place', ''), a.get('start_num', ''), a.get('name', ''),
        a.get('region', ''), a.get('year', ''),
        a.get('clean_time', ''), a.get('rank_qual', ''),
        a.get('l1', ''), a.get('l2', ''), a.get('l3', ''), a.get('sum', ''),
        a.get('time', ''), a.get('lost', ''), a.get('points', ''),
        a.get('rank_final', ''), a.get('note', ''),
    ]


def _build_csv(tech, athletes) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    w.writerow(['Техническая информация'])
    for item in tech:
        w.writerow([item.get('key', ''), item.get('value', '')])
    w.writerow([])
    w.writerow(['Спортсмены'])
    w.writerow(ATHLETE_HEADER)
    for a in athletes:
        w.writerow(_athlete_row(a))
    return '\ufeff' + buf.getvalue()


def _build_xlsx(tech, athletes) -> bytes:
    wb = Workbook()
    ws1 = wb.active
    ws1.title = 'Техническая информация'
    for item in tech:
        ws1.append([item.get('key', ''), item.get('value', '')])
    ws2 = wb.create_sheet('Спортсмены')
    ws2.append(ATHLETE_HEADER)
    for a in athletes:
        ws2.append(_athlete_row(a))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()