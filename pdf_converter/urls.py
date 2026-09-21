from django.urls import path
from . import views

app_name = 'pdf_converter'

urlpatterns = [
    path('', views.index, name='index'),
    path('convert/', views.convert, name='convert'),
]