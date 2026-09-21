from django.urls import path
from . import views

app_name = 'leader_track'

urlpatterns = [
    path('', views.leaders, name='leaders'),
]