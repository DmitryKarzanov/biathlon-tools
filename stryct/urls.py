from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('homepage.urls')),
    path('leader_track/', include('leader_track.urls')),
    path('pdf_converter/', include('pdf_converter.urls'))
]
