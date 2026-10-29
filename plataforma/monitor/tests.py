from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Agente, Cliente, Equipamento, LinkWAN

LINKS_VAZIO = {"links-TOTAL_FORMS": "0", "links-INITIAL_FORMS": "0",
               "links-MIN_NUM_FORMS": "0", "links-MAX_NUM_FORMS": "1000"}


def equip_post(cliente, **extra):
    dados = {"cliente": cliente.pk, "nome": "Câmera", "categoria": "camera", "fabricante": "hikvision",
             "driver": "auto", "ip": "192.168.1.101", "porta_http": "80", "ordem": "0",
             "ativo": "on", "quantidade": "1", "numero_inicial": "1", **LINKS_VAZIO}
    dados.update(extra)
    return dados


class CadastrosTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staff", password="x", is_staff=True)
        self.client.force_login(self.staff)
        self.cli = Cliente.objects.create(nome="Condomínio Azul", chave="MS-0001")
        self.outro = Cliente.objects.create(nome="Outro", chave="MS-0002")

    def test_paginas_abrem(self):
        ag = Agente.objects.create(cliente=self.cli)
        eq = Equipamento.objects.create(cliente=self.cli, nome="GW", categoria="gateway", ip="10.0.0.1")
        for nome, args in [("cad_clientes", []), ("cad_cliente_novo", []), ("cad_cliente", [self.cli.pk]),
                           ("cad_cliente_excluir", [self.cli.pk]), ("cad_agentes", []),
                           ("cad_agente_novo", []), ("cad_agente", [ag.pk]), ("cad_agente_excluir", [ag.pk]),
                           ("cad_equipamentos", []), ("cad_equipamento_novo", []),
                           ("cad_equipamento", [eq.pk]), ("cad_equipamento_excluir", [eq.pk]),
                           ("cad_usuarios", []), ("cad_usuario_novo", []), ("cad_usuario", [self.staff.pk])]:
            with self.subTest(nome):
                self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 200)
        r = self.client.get(reverse("cad_equipamentos") + f"?cliente={self.cli.pk}&categoria=gateway&q=GW")
        self.assertContains(r, "10.0.0.1")

    def test_so_staff(self):
        comum = User.objects.create_user("comum", password="x")
        self.client.force_login(comum)
        self.assertEqual(self.client.get(reverse("cad_clientes")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("cad_clientes")).status_code, 302)

    def test_cliente_criar_editar_excluir(self):
        r = self.client.post(reverse("cad_cliente_novo"), {"chave": "MS-0003", "nome": "Novo", "ativo": "on"})
        novo = Cliente.objects.get(chave="MS-0003")
        self.assertRedirects(r, reverse("cad_cliente", args=[novo.pk]))
        self.client.post(reverse("cad_cliente", args=[novo.pk]), {"chave": "MS-0003", "nome": "Novo 2"})
        novo.refresh_from_db()
        self.assertEqual((novo.nome, novo.ativo), ("Novo 2", False))
        r = self.client.post(reverse("cad_cliente_novo"), {"chave": "MS-0003", "nome": "Duplicado"})
        self.assertEqual(r.status_code, 200)  # chave repetida volta com erro
        self.client.post(reverse("cad_cliente_excluir", args=[novo.pk]))
        self.assertFalse(Cliente.objects.filter(pk=novo.pk).exists())

    def test_agente_ini_e_token(self):
        r = self.client.post(reverse("cad_agente_novo"), {"cliente": self.cli.pk, "nome": "Principal",
                                                          "intervalo_segundos": "30",
                                                          "falhas_para_offline": "3", "ativo": "on"})
        ag = Agente.objects.get(cliente=self.cli)
        self.assertRedirects(r, reverse("cad_agente", args=[ag.pk]))
        r = self.client.get(reverse("cad_agente", args=[ag.pk]))
        self.assertContains(r, f"token = {ag.token}")
        self.assertContains(r, "url = http://testserver")
        antigo = ag.token
        self.client.post(reverse("cad_agente_token", args=[ag.pk]))
        ag.refresh_from_db()
        self.assertNotEqual(ag.token, antigo)

    def test_equipamento_senha_cifrada_e_mantida(self):
        self.client.post(reverse("cad_equipamento_novo"), equip_post(self.cli, senha="segredo"))
        eq = Equipamento.objects.get()
        self.assertEqual(eq.senha, "segredo")
        self.assertNotIn("segredo", eq.senha_cifrada)
        self.client.post(reverse("cad_equipamento", args=[eq.pk]), equip_post(self.cli, nome="Cam X"))
        eq.refresh_from_db()
        self.assertEqual((eq.nome, eq.senha), ("Cam X", "segredo"))

    def test_lote_de_cameras(self):
        r = self.client.post(reverse("cad_equipamento_novo"),
                             equip_post(self.cli, nome="Câmera", quantidade="3", senha="s"))
        self.assertRedirects(r, reverse("cad_equipamentos") + f"?cliente={self.cli.pk}")
        cams = list(Equipamento.objects.order_by("ip").values_list("nome", "ip"))
        self.assertEqual(cams, [("Câmera 01", "192.168.1.101"), ("Câmera 02", "192.168.1.102"),
                                ("Câmera 03", "192.168.1.103")])
        self.assertTrue(all(e.senha == "s" for e in Equipamento.objects.all()))

    def test_agente_de_outro_cliente_recusado(self):
        ag = Agente.objects.create(cliente=self.outro)
        r = self.client.post(reverse("cad_equipamento_novo"), equip_post(self.cli, agente=ag.pk))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Equipamento.objects.exists())

    def test_links_do_gateway(self):
        dados = equip_post(self.cli, nome="Router", categoria="gateway", fabricante="mikrotik", ip="10.0.0.1")
        dados.update({"links-TOTAL_FORMS": "1", "links-0-interface": "ether1", "links-0-nome": "Vivo",
                      "links-0-download_mbps": "500"})
        self.client.post(reverse("cad_equipamento_novo"), dados)
        link = LinkWAN.objects.get()
        self.assertEqual((link.interface, link.download_mbps, link.equipamento.nome), ("ether1", 500, "Router"))

    def test_usuario(self):
        r = self.client.post(reverse("cad_usuario_novo"), {"username": "op", "senha1": "senha-forte-1",
                                                           "senha2": "senha-forte-1", "is_active": "on"})
        self.assertRedirects(r, reverse("cad_usuarios"))
        op = User.objects.get(username="op")
        self.assertTrue(op.check_password("senha-forte-1"))
        self.assertFalse(op.is_staff)
        r = self.client.post(reverse("cad_usuario_novo"), {"username": "x", "senha1": "a1234567",
                                                           "senha2": "b1234567"})
        self.assertFalse(User.objects.filter(username="x").exists())
        # O próprio usuário não consegue tirar o próprio acesso nem se excluir.
        self.client.post(reverse("cad_usuario", args=[self.staff.pk]), {"username": "staff"})
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_staff and self.staff.is_active)
        self.client.post(reverse("cad_usuario_excluir", args=[self.staff.pk]))
        self.assertTrue(User.objects.filter(pk=self.staff.pk).exists())
