from django.contrib.auth import views as auth_views
from django.urls import path

from . import api_agente, cadastros, views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("cliente/<str:chave>/", views.cliente, name="cliente"),
    path("agentes/", views.agentes, name="agentes"),

    path("cadastros/", cadastros.inicio, name="cadastros"),
    path("cadastros/clientes/", cadastros.clientes, name="cad_clientes"),
    path("cadastros/clientes/novo/", cadastros.cliente_form, name="cad_cliente_novo"),
    path("cadastros/clientes/<int:pk>/", cadastros.cliente_form, name="cad_cliente"),
    path("cadastros/clientes/<int:pk>/excluir/", cadastros.cliente_excluir, name="cad_cliente_excluir"),
    path("cadastros/agentes/", cadastros.agentes, name="cad_agentes"),
    path("cadastros/agentes/novo/", cadastros.agente_form, name="cad_agente_novo"),
    path("cadastros/agentes/<int:pk>/", cadastros.agente_form, name="cad_agente"),
    path("cadastros/agentes/<int:pk>/token/", cadastros.agente_token, name="cad_agente_token"),
    path("cadastros/agentes/<int:pk>/excluir/", cadastros.agente_excluir, name="cad_agente_excluir"),
    path("cadastros/equipamentos/", cadastros.equipamentos, name="cad_equipamentos"),
    path("cadastros/equipamentos/novo/", cadastros.equipamento_form, name="cad_equipamento_novo"),
    path("cadastros/equipamentos/<int:pk>/", cadastros.equipamento_form, name="cad_equipamento"),
    path("cadastros/equipamentos/<int:pk>/excluir/", cadastros.equipamento_excluir,
         name="cad_equipamento_excluir"),
    path("cadastros/usuarios/", cadastros.usuarios, name="cad_usuarios"),
    path("cadastros/usuarios/novo/", cadastros.usuario_form, name="cad_usuario_novo"),
    path("cadastros/usuarios/<int:pk>/", cadastros.usuario_form, name="cad_usuario"),
    path("cadastros/usuarios/<int:pk>/excluir/", cadastros.usuario_excluir, name="cad_usuario_excluir"),
    path("entrar/", auth_views.LoginView.as_view(template_name="monitor/login.html"), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),

    path("api/busca/", views.api_busca, name="api_busca"),
    path("api/dashboard/", views.api_dashboard, name="api_dashboard"),
    path("api/cliente/<str:chave>/", views.api_cliente, name="api_cliente"),
    path("api/agentes/", views.api_agentes, name="api_agentes"),
    path("api/agentes/<int:pk>/comando/", views.api_agente_comando, name="api_agente_comando"),

    path("api/agente/config/", api_agente.config, name="api_agente_config"),
    path("api/agente/relatorio/", api_agente.relatorio, name="api_agente_relatorio"),
    path("api/agente/eventos/", api_agente.eventos, name="api_agente_eventos"),
]
