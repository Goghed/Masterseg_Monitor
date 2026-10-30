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
                           ("cad_equipamento_novo", []),
                           ("cad_equipamento", [eq.pk]), ("cad_equipamento_excluir", [eq.pk]),
                           ("cad_usuarios", []), ("cad_usuario_novo", []), ("cad_usuario", [self.staff.pk])]:
            with self.subTest(nome):
                self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 200)
        # Os equipamentos ficam dentro do cliente, agrupados por categoria.
        r = self.client.get(reverse("cad_cliente", args=[self.cli.pk]))
        self.assertContains(r, "10.0.0.1")
        self.assertContains(r, "Gateway / roteador")
        self.assertNotContains(self.client.get(reverse("cad_cliente", args=[self.outro.pk])), "10.0.0.1")
        r = self.client.get(reverse("cad_equipamentos") + f"?cliente={self.cli.pk}")
        self.assertRedirects(r, reverse("cad_cliente", args=[self.cli.pk]) + "#equipamentos",
                             fetch_redirect_response=False)

    def test_so_staff(self):
        comum = User.objects.create_user("comum", password="x")
        self.client.force_login(comum)
        self.assertEqual(self.client.get(reverse("cad_clientes")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("cad_clientes")).status_code, 302)

    def test_cliente_criar_editar_excluir(self):
        lista = self.client.get(reverse("cad_clientes"))
        url = reverse("cad_cliente", args=[self.cli.pk])
        self.assertContains(lista, f'data-href="{url}"')
        self.assertContains(lista, f'href="{url}">Condomínio Azul</a>', html=False)
        r = self.client.post(reverse("cad_cliente_novo"), {"chave": "MS-0003", "nome": "Novo", "ativo": "on"})
        novo = Cliente.objects.get(chave="MS-0003")
        self.assertRedirects(r, reverse("cad_cliente", args=[novo.pk]) + "#equipamentos",
                             fetch_redirect_response=False)
        self.assertEqual(novo.agentes.count(), 1)  # cliente novo já nasce com o agente
        self.client.post(reverse("cad_cliente", args=[novo.pk]), {"chave": "MS-0003", "nome": "Novo 2"})
        novo.refresh_from_db()
        self.assertEqual((novo.nome, novo.ativo), ("Novo 2", False))
        r = self.client.post(reverse("cad_cliente_novo"), {"chave": "MS-0003", "nome": "Duplicado"})
        self.assertEqual(r.status_code, 200)  # chave repetida volta com erro
        self.client.post(reverse("cad_cliente_excluir", args=[novo.pk]))
        self.assertFalse(Cliente.objects.filter(pk=novo.pk).exists())

    def test_cliente_gold(self):
        self.client.post(reverse("cad_cliente", args=[self.cli.pk]),
                         {"chave": "MS-0001", "nome": "Condomínio Azul", "gold": "on", "ativo": "on"})
        self.cli.refresh_from_db()
        self.assertTrue(self.cli.gold)
        self.assertContains(self.client.get(reverse("cliente", args=[self.cli.chave])), 'class="medalha"')
        self.assertNotContains(self.client.get(reverse("cliente", args=[self.outro.chave])), 'class="medalha"')
        r = self.client.get(reverse("cliente", args=[self.outro.chave]))
        self.assertContains(r, 'data-staff="true"')
        self.assertContains(r, "Editar cliente")
        self.assertContains(r, "app.css?v=")
        gold = {m["chave"]: m["gold"] for m in self.client.get("/api/dashboard/").json()["mosaico"]}
        self.assertEqual(gold, {"MS-0001": True, "MS-0002": False})

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

    def test_pacote_do_agente(self):
        import io
        import zipfile
        ag = Agente.objects.create(cliente=self.cli)
        r = self.client.get(reverse("cad_agente_pacote", args=[ag.pk]))
        self.assertEqual(r["Content-Type"], "application/zip")
        self.assertIn('filename="masterseg-agente-ms-0001.zip"', r["Content-Disposition"])
        z = zipfile.ZipFile(io.BytesIO(r.content))
        nomes = set(z.namelist())
        for arquivo in ("instalar.bat", "instalar.ps1", "agente.py", "verificacoes.py",
                        "requirements.txt", "drivers/__init__.py", "agente.ini"):
            self.assertIn(f"masterseg-agente-ms-0001/{arquivo}", nomes)
        ini = z.read("masterseg-agente-ms-0001/agente.ini").decode()
        self.assertIn(f"token = {ag.token}\r\n", ini)
        self.assertIn("url = http://testserver\r\n", ini)
        bat = z.read("masterseg-agente-ms-0001/instalar.bat")
        self.assertNotIn(b"\n", bat.replace(b"\r\n", b""))  # só CRLF
        self.assertTrue(z.read("masterseg-agente-ms-0001/instalar.ps1").startswith(b"\xef\xbb\xbf"))

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
        self.assertRedirects(r, reverse("cad_cliente", args=[self.cli.pk]) + "#equipamentos",
                             fetch_redirect_response=False)
        cams = list(Equipamento.objects.order_by("ip").values_list("nome", "ip"))
        self.assertEqual(cams, [("Câmera 01", "192.168.1.101"), ("Câmera 02", "192.168.1.102"),
                                ("Câmera 03", "192.168.1.103")])
        self.assertTrue(all(e.senha == "s" for e in Equipamento.objects.all()))

    def test_lote_de_outras_categorias(self):
        self.client.post(reverse("cad_equipamento_novo"),
                         equip_post(self.cli, nome="DVR", categoria="gravador", quantidade="2", ip="10.0.0.20"))
        self.assertEqual(list(Equipamento.objects.order_by("nome").values_list("nome", "ip")),
                         [("DVR 01", "10.0.0.20"), ("DVR 02", "10.0.0.21")])
        r = self.client.post(reverse("cad_equipamento_novo"),
                             equip_post(self.cli, nome="GW", categoria="gateway", quantidade="2", ip="10.0.0.1"))
        self.assertContains(r, "Cadastro em lote está disponível para")
        self.assertEqual(Equipamento.objects.count(), 2)

    def test_equipamento_preso_ao_cliente(self):
        meu = Agente.objects.create(cliente=self.cli, nome="Meu")
        Agente.objects.create(cliente=self.outro, nome="Alheio")
        r = self.client.get(reverse("cad_equipamento_novo") + f"?cliente={self.cli.pk}&categoria=gravador")
        self.assertContains(r, f'type="hidden" name="cliente" value="{self.cli.pk}"')
        self.assertContains(r, ">Meu (MS-0001)<")
        self.assertNotContains(r, "Alheio")
        self.assertContains(r, '<option value="gravador" selected>')
        self.assertContains(r, f"Voltar para {self.cli.nome}")
        self.assertEqual(meu.cliente, self.cli)

    def test_duplicar_equipamento(self):
        ag = Agente.objects.create(cliente=self.cli)
        dvr = Equipamento(cliente=self.cli, agente=ag, nome="DVR 1", categoria="gravador", fabricante="hikvision",
                          modelo="DS-7632", ip="10.0.0.11", porta_http=8080, usuario="admin", critico=True)
        dvr.senha = "segredo"
        dvr.save()
        url = reverse("cad_equipamento_novo") + f"?copiar={dvr.pk}"
        r = self.client.get(url)
        self.assertContains(r, "Copiando a configuração de")
        self.assertContains(r, 'value="DS-7632"')
        self.assertContains(r, 'value="8080"')
        self.assertContains(r, f'type="hidden" name="cliente" value="{self.cli.pk}"')
        # Só nome e IP mudam; a senha vem do original.
        self.client.post(url, equip_post(self.cli, nome="DVR 2", ip="10.0.0.12", categoria="gravador",
                                         fabricante="hikvision", modelo="DS-7632", porta_http="8080",
                                         usuario="admin", agente=ag.pk, critico="on"))
        novo = Equipamento.objects.get(nome="DVR 2")
        self.assertEqual((novo.ip, novo.modelo, novo.usuario, novo.senha, novo.critico),
                         ("10.0.0.12", "DS-7632", "admin", "segredo", True))

        gw = Equipamento.objects.create(cliente=self.cli, nome="GW", categoria="gateway", ip="10.0.0.1")
        LinkWAN.objects.create(equipamento=gw, interface="pppoe-out1", nome="Vivo", download_mbps=400)
        r = self.client.get(reverse("cad_equipamento_novo") + f"?copiar={gw.pk}")
        self.assertContains(r, 'name="links-TOTAL_FORMS" value="1"')
        self.assertContains(r, 'value="pppoe-out1"')

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

    def test_interfaces_do_roteador_sugeridas(self):
        gw = Equipamento.objects.create(cliente=self.cli, nome="GW", categoria="gateway", ip="10.0.0.1",
                                        detalhes={"interfaces": [{"nome": "pppoe-out 1-Vivo", "tipo": "pppoe-out",
                                                                  "running": True, "comentario": ""}]})
        r = self.client.get(reverse("cad_equipamento", args=[gw.pk]))
        self.assertContains(r, 'list="interfaces-roteador"')
        self.assertContains(r, '<option value="pppoe-out 1-Vivo">')

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


class RelatorioTest(TestCase):
    def test_host_inacessivel_do_agente_antigo_vira_offline(self):
        from .models import Status
        from .servicos import processar_relatorio
        cli = Cliente.objects.create(nome="Fazenda", chave="5053")
        ag = Agente.objects.create(cliente=cli)
        cams = [Equipamento.objects.create(cliente=cli, nome=f"C-{n}", categoria="camera", ip=f"192.168.1.{100 + n}")
                for n in (29, 30, 31)]
        # O que o agente 1.0.3 mandava: "respondeu" ao ping (host inacessível), sem tempo de resposta.
        processar_relatorio(ag, {"resultados": [
            {"id": cams[0].pk, "online": True, "latencia_ms": None, "perda_pct": 66.0, "servico_ok": False},
            {"id": cams[1].pk, "online": True, "latencia_ms": None, "perda_pct": 0.0, "servico_ok": False},
            {"id": cams[2].pk, "online": False, "latencia_ms": None, "perda_pct": 100.0},
        ]})
        for c in cams:
            c.refresh_from_db()
        self.assertEqual({c.status for c in cams}, {Status.OFFLINE})
        self.assertEqual({c.perda_pct for c in cams}, {100.0})

    def test_central_de_alarme(self):
        from .models import Evento
        from .servicos import processar_relatorio
        cli = Cliente.objects.create(nome="Xapada", chave="4023")
        ag = Agente.objects.create(cliente=cli)
        central = Equipamento.objects.create(cliente=cli, nome="Central 01", categoria="alarme", ip="10.0.1.21",
                                             canais_em_uso=4)

        def relatorio(arming, disparando, zona_estado="normal"):
            processar_relatorio(ag, {"resultados": [{
                "id": central.pk, "online": True, "latencia_ms": 1.0, "perda_pct": 0,
                "detalhes": {
                    "canais": [{"id": str(i), "online": i <= 2} for i in range(1, 33)],
                    "alarme": {"particoes": [{"id": "1", "nome": "Perímetro", "estado": arming,
                                              "disparando": disparando}],
                               "zonas": [{"id": "0", "numero": 1, "nome": "Portão", "particao": "1",
                                          "estado": zona_estado}]}}}]})

        relatorio("desarmada", False)
        relatorio("armada", False)
        relatorio("armada", True, "disparada")
        disparo = Evento.objects.get(tipo=Evento.Tipo.ALARME_DISPARO)
        self.assertIn("zona 1 (Portão)", disparo.detalhe)
        self.assertIsNone(disparo.resolvido_em)
        relatorio("desarmada", False)
        disparo.refresh_from_db()
        self.assertIsNotNone(disparo.resolvido_em)
        estados = list(Evento.objects.filter(tipo=Evento.Tipo.ALARME_ESTADO).order_by("criado_em", "pk")
                       .values_list("titulo", flat=True))
        self.assertEqual(estados, ["Central 01: Perímetro armada", "Central 01: Perímetro desarmada"])
        self.assertFalse(Evento.objects.filter(tipo=Evento.Tipo.ALARME_ESTADO).first().e_problema)
        # Só os 4 canais em uso contam: 2 sem sinal geram evento, os outros 28 não.
        self.assertEqual(Evento.objects.filter(tipo=Evento.Tipo.CANAL_OFFLINE).count(), 2)
        staff = User.objects.create_user("s", password="x", is_staff=True)
        self.client.force_login(staff)
        item = self.client.get(f"/api/cliente/{cli.chave}/").json()["secoes"][0]["itens"][0]
        self.assertEqual(len(item["detalhes"]["canais"]), 4)
        self.assertEqual(item["detalhes"]["alarme"]["particoes"][0]["estado"], "desarmada")

    def test_leitor_facial_e_pabx(self):
        from .models import Evento
        from .servicos import processar_relatorio
        cli = Cliente.objects.create(nome="Prédio", chave="7000")
        ag = Agente.objects.create(cliente=cli)
        facial = Equipamento.objects.create(cliente=cli, nome="Facial Hall", categoria="facial", ip="10.0.2.10")
        pabx = Equipamento.objects.create(cliente=cli, nome="CIP 850", categoria="pabx", ip="10.0.2.2")

        def relatorio(violado):
            processar_relatorio(ag, {"resultados": [
                {"id": facial.pk, "online": True, "latencia_ms": 2.0, "perda_pct": 0,
                 "detalhes": {"acesso": {"portas": [{"id": "1", "estado": "fechada"}], "violado": violado}}},
                {"id": pabx.pk, "online": True, "latencia_ms": 1.0, "perda_pct": 0,
                 "detalhes": {"sip": {"responde": True, "porta": 5060, "latencia_ms": 3.0, "servidor": "CIP"}}},
            ]})

        relatorio(False)
        relatorio(True)
        relatorio(True)
        self.assertEqual(Evento.objects.filter(equipamento=facial, referencia="violacao").count(), 1)
        staff = User.objects.create_user("s", password="x", is_staff=True)
        self.client.force_login(staff)
        secoes = {s["chave"]: [i["nome"] for i in s["itens"]]
                  for s in self.client.get(f"/api/cliente/{cli.chave}/").json()["secoes"]}
        self.assertEqual(secoes, {"seguranca": ["Facial Hall"], "comunicacao": ["CIP 850"]})
