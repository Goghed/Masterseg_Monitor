from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "MasterSeg Monitor"
admin.site.site_title = "MasterSeg Monitor"
admin.site.index_title = "Cadastros"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("monitor.urls")),
]
