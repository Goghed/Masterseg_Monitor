from django.contrib.auth import views as auth_views
from django.urls import path

from . import api_agente, views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("cliente/<str:chave>/", views.cliente, name="cliente"),
    path("entrar/", auth_views.LoginView.as_view(template_name="monitor/login.html"), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),

    path("api/busca/", views.api_busca, name="api_busca"),
    path("api/dashboard/", views.api_dashboard, name="api_dashboard"),
    path("api/cliente/<str:chave>/", views.api_cliente, name="api_cliente"),

    path("api/agente/config/", api_agente.config, name="api_agente_config"),
    path("api/agente/relatorio/", api_agente.relatorio, name="api_agente_relatorio"),
    path("api/agente/eventos/", api_agente.eventos, name="api_agente_eventos"),
]
