from django.shortcuts import render


def leaders(request):
    """Страница отслеживания лидеров."""
    return render(request, 'leader_track/leaders.html')