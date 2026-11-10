import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from radar import config, monitoramento as mon, navegador as nav, perfis

from conftest import ApifyFalso, conta, post

CAPTURAS_PERFIL = [tuple(c) for c in json.loads(
    (Path(__file__).parent / "dados" / "visita_perfil.json").read_text(encoding="utf-8"))]


class ClienteFalso:
    """Faz o papel da página do Instagram: visitas, rolagens e buscas com respostas fixas."""

    def __init__(self, visitas=None, rolagens=None, buscas=None):
        self.visitas = visitas or {}
        self.rolagens = list(rolagens or [])
        self.buscas = buscas or {}
        self.pedidos = []

    def visitar_perfil(self, username, parecidas=True):
        self.pedidos.append(("perfil", username))
        resposta = self.visitas.get(username, {"user": None, "posts": [], "tem_mais": False, "parecidas": [],
                                               "status": "nao_encontrado"})
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    def rolar(self):
        self.pedidos.append(("rolar", None))
        return self.rolagens.pop(0) if self.rolagens else ([], False)

    def pesquisar(self, termo):
        self.pedidos.append(("busca", termo))
        return self.buscas.get(termo, [])


def navegador(tmp_path, **cliente) -> tuple[nav.Navegador, ClienteFalso]:
    falso = ClienteFalso(**cliente)
    return nav.Navegador(falso, nav.Ritmo(dormir=lambda _: None), raw_dir=tmp_path), falso


def no(pk: int, dias_atras: float, fixado: bool = False, **extra) -> dict:
    """Post da grade do perfil, como o site manda."""
    quando = datetime.now(timezone.utc) - timedelta(days=dias_atras)
    item = {"pk": str(pk), "code": f"C{pk}", "media_type": 1, "taken_at": int(quando.timestamp()),
            "caption": {"text": f"Post {pk}"}, "like_count": 10, "comment_count": 2,
            "user": {"username": "portal"}, "timeline_pinned_user_ids": ["1"] if fixado else []}
    return item | extra


def visita(nos: list[dict], tem_mais: bool) -> dict:
    return {"user": {"username": "portal", "follower_count": 1}, "posts": nos, "tem_mais": tem_mais,
            "parecidas": [], "status": None}


def test_shortcode_vira_o_id_do_post():
    assert nav.shortcode_para_pk("DfLduMaxvu4") == "4020437810137201592"  # par real da base


def test_visita_ao_perfil_vira_item_da_apify(con, municipios, tmp_path):
    lida = nav.ler_visita(CAPTURAS_PERFIL, "maria.palmas")
    assert lida["tem_mais"] and len(lida["posts"]) == 2 and len(lida["parecidas"]) == 2
    coletor, _ = navegador(tmp_path, visitas={"maria.palmas": lida})
    _, itens = coletor.rodar(config.ATOR_PERFIL, {"usernames": ["maria.palmas", "sumiu"]}, "perfil")
    maria, sumiu = itens
    assert sumiu == {"username": "sumiu", "error": "not_found"}
    assert (maria["username"], maria["followersCount"], maria["id"], maria["postsCount"]) == \
        ("maria.palmas", 54321, "123456789", 950)
    fixado, video = maria["latestPosts"]
    assert fixado["isPinned"] and fixado["type"] == "Sidecar" and fixado["caption"] is None
    assert video["type"] == "Video" and video["likesCount"] == 1500 and video["videoViewCount"] == 30000
    assert video["timestamp"] == "2025-11-01T00:00:00.000Z" and video["locationName"] == "Palmas, Tocantins"
    assert perfis.relacionadas(maria) == [("joao.to", "João TO")]  # o privado fica de fora

    bruto = json.loads(next(tmp_path.glob("*_perfil_*.json")).read_text(encoding="utf-8"))
    assert bruto["via"] == "navegador" and bruto["usd"] == 0 and len(bruto["itens"]) == 2
    assert perfis.ingerir(con, maria, "influentes", municipios) == "maria.palmas"
    assert con.execute("SELECT local FROM posts WHERE post_id = '4020437810137201592'").fetchone()[0] == \
        "Palmas, Tocantins"


def test_posts_rola_ate_a_data_sem_parar_no_fixado(tmp_path):
    coletor, cliente = navegador(
        tmp_path, visitas={"portal": visita([no(1, 90, fixado=True), no(2, 1), no(3, 5)], True)},
        rolagens=[([no(4, 9), no(5, 20), no(6, 25)], True), ([no(7, 30)], True)])
    desde = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    _, itens = coletor.rodar(config.ATOR_POSTS, {"username": ["portal"], "onlyPostsNewerThan": desde}, "historico")
    assert [p["id"] for p in itens] == ["2", "3", "4"]  # o fixado antigo sai sem interromper; o 5 passa do corte
    assert cliente.pedidos == [("perfil", "portal"), ("rolar", None)]
    assert all(p["ownerUsername"] == "portal" for p in itens) and coletor.leituras == 2


def test_posts_respeitam_limite_e_desde_por_conta(tmp_path):
    recentes = visita([no(1, 0.5), no(2, 1.5), no(3, 2.5)], False)
    coletor, _ = navegador(tmp_path, visitas={"a": recentes})
    _, itens = coletor.rodar(config.ATOR_POSTS, {"username": ["a"], "onlyPostsNewerThan": "2000-01-01",
                                                 "resultsLimit": 2}, "historico")
    assert len(itens) == 2
    desde = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _, itens = coletor.rodar(config.ATOR_POSTS, {"username": ["a"], "onlyPostsNewerThan": "2000-01-01",
                                                 "desde_por_conta": {"a": desde}}, "monitor_posts")
    assert [p["id"] for p in itens] == ["1"]


def test_pesquisa_pela_caixa_de_busca(tmp_path):
    coletor, _ = navegador(tmp_path, buscas={"palmas": [
        {"user": {"pk": "5", "username": "PalmasTop_", "full_name": "Palmas Top", "is_verified": True}},
        {"pk": "6", "username": "outra", "full_name": "Outra"}]})
    assert [u["username"] for u in coletor.pesquisar("palmas")] == ["palmastop_", "outra"]
    assert next(tmp_path.glob("*_pesquisa_*.json"))


@pytest.mark.parametrize("captura", [(429, None),
                                     (200, {"message": "Please wait a few minutes before you try again."}),
                                     (400, {"message": "checkpoint_required", "status": "fail"}),
                                     (200, {"require_login": True})])
def test_aviso_do_instagram_para_a_coleta(captura):
    with pytest.raises(nav.ColetaInterrompida, match="pausa"):
        nav.ler_visita([captura], "x")


def test_depois_do_aviso_nenhuma_leitura_a_mais(tmp_path):
    coletor, cliente = navegador(tmp_path, visitas={"a": nav.ColetaInterrompida("O Instagram pediu uma pausa")})
    with pytest.raises(nav.ColetaInterrompida):
        coletor.rodar(config.ATOR_PERFIL, {"usernames": ["a", "b"]}, "perfil")
    with pytest.raises(nav.ColetaInterrompida):
        coletor.perfil("c")
    assert cliente.pedidos == [("perfil", "a")]
    assert not list(tmp_path.glob("*.json"))  # o lote interrompido não vira bruto pela metade


def test_paginas_seguidas_sem_dados_param_a_coleta(tmp_path):
    vazia = {"user": None, "posts": [], "tem_mais": False, "parecidas": [], "status": "sem_dados"}
    coletor, _ = navegador(tmp_path, visitas={"x": vazia})
    for _ in range(nav.MAX_FALHAS_SEGUIDAS - 1):
        assert coletor.perfil("x")["error"] == "not_found"
    with pytest.raises(nav.ColetaInterrompida, match="sem dados"):
        coletor.perfil("x")


class PaginaFalsa:
    def __init__(self, url="https://www.instagram.com/", fechada=False):
        self.url = url
        self.fechada = fechada

    def wait_for_timeout(self, _):
        if self.fechada:
            raise RuntimeError("Target page, context or browser has been closed")


@pytest.mark.parametrize("pagina, trecho", [(PaginaFalsa(fechada=True), "parou de responder"),
                                            (PaginaFalsa("https://www.instagram.com/challenge/123/"), "levou")])
def test_janela_fechada_ou_desafio_interrompem(pagina, trecho):
    instagram = nav.PaginaInstagram()
    instagram.pagina = pagina
    with pytest.raises(nav.ColetaInterrompida, match=trecho):
        instagram._esperar(0.1)


def test_ritmo_pausa_e_respeita_os_tetos(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NAV_MAX_HORA", 3)
    monkeypatch.setattr(config, "NAV_MAX_DIA", 5)
    monkeypatch.setattr(config, "NAV_LONGA_A_CADA", 100)
    agora = [1_000_000.0]
    pausas = []

    def dormir(segundos):
        pausas.append(segundos)
        agora[0] += segundos

    arquivo = tmp_path / "leituras.json"
    ritmo = nav.Ritmo(arquivo, dormir, lambda: agora[0])
    for _ in range(4):
        ritmo.esperar()
    assert pausas[0] <= config.NAV_PAUSA[1] and pausas[-1] > 3000  # a 4ª leitura espera a hora virar
    novo = nav.Ritmo(arquivo, dormir, lambda: agora[0])  # outra execução lê o que já foi feito
    novo.esperar()
    with pytest.raises(nav.TetoAtingido, match="Teto") as erro:
        novo.esperar()
    # Volta a caber quando a leitura mais velha sai da janela de 24 horas.
    assert erro.value.espera == pytest.approx(novo.leituras[0] + 86400 - agora[0])
    agora[0] += erro.value.espera
    assert novo.espera_do_teto() == 0


def test_coletar_novos_le_ate_o_ultimo_post_e_registra_a_visita(con, municipios):
    conta(con, municipios, "portal", [post(10, codigo="V1")])
    conta(con, municipios, "outro", [])
    con.execute("UPDATE contas SET atualizada_em = '2020-01-01T00:00:00+00:00' WHERE username = 'outro'")
    novos = [post(0, codigo="N1") | {"ownerUsername": "portal"}]
    coletor = ApifyFalso({config.ATOR_POSTS: novos, config.ATOR_PERFIL: []})
    agora = datetime.now(timezone.utc)
    r = mon.coletar_novos(con, coletor, mon.monitoradas(con), aviso=lambda _: None, agora=agora)
    assert r["posts"] == 1 and r["contas"] == 2
    entrada = coletor.chamadas[0][1]
    # Último post há 10 dias: lê desde ele. Sem posts: só a janela de 3 dias.
    assert entrada["desde_por_conta"]["portal"][:10] == (agora - timedelta(days=10)).date().isoformat()
    assert entrada["desde_por_conta"]["outro"][:10] == (agora - mon.JANELA_ATUALIZACAO).date().isoformat()
    assert coletor.chamadas[1] == (config.ATOR_PERFIL, {"usernames": ["outro"]})  # perfil velho: relido
    assert con.execute("SELECT COUNT(*) FROM visitas").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM posts WHERE post_id = 'id-N1'").fetchone()[0] == 1
    # A próxima rodada começa por quem ficou sem visita.
    con.execute("DELETE FROM visitas WHERE username = 'outro'")
    assert [c["username"] for c in mon.ordem_de_visita(con, mon.monitoradas(con))][0] == "outro"


def test_sessao_lida_pelo_nome_do_cookie(tmp_path):
    pasta = tmp_path / "perfil"
    assert nav.tem_sessao(pasta) is False
    arquivo = pasta / "Default" / "Network" / "Cookies"
    arquivo.parent.mkdir(parents=True)
    banco = sqlite3.connect(arquivo)
    banco.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB)")
    banco.execute("INSERT INTO cookies VALUES ('.instagram.com', 'csrftoken', x'00')")
    banco.commit()
    assert nav.tem_sessao(pasta) is False  # cookies básicos do site: ainda sem login
    banco.execute("INSERT INTO cookies VALUES ('.instagram.com', 'sessionid', x'00')")
    banco.commit()
    banco.close()
    assert nav.tem_sessao(pasta) is True


def test_monitoramento_so_rola_por_post_novo_e_atualiza_a_primeira_pagina(tmp_path):
    # Portal que publica muito: 12 posts de hoje e de ontem na primeira página, mais na rolagem.
    primeira = [no(i, i / 10) for i in range(1, 13)]
    coletor, cliente = navegador(tmp_path, visitas={"portal": visita(primeira, True)},
                                 rolagens=[([no(20, 2.5), no(21, 2.8)], True)])
    ultimo = (datetime.now(timezone.utc) - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    tres_dias = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _, itens = coletor.rodar(config.ATOR_POSTS, {"username": ["portal"], "onlyPostsNewerThan": "2000-01-01",
                                                 "desde_por_conta": {"portal": ultimo},
                                                 "atualizar_desde": tres_dias}, "monitor_posts")
    # Os 5 posts novos (menos de 12 h) e os 7 já gravados da primeira página (engajamento de graça); sem rolar.
    assert len(itens) == 12 and cliente.pedidos == [("perfil", "portal")] and coletor.leituras == 1


def test_perfil_da_semana_sai_da_propria_visita(con, municipios, tmp_path):
    conta(con, municipios, "portal", [post(10, codigo="V1")])
    con.execute("UPDATE contas SET atualizada_em = '2020-01-01T00:00:00+00:00'")
    coletor, cliente = navegador(tmp_path, visitas={"portal": visita([no(1, 0.1)], False) | {
        "user": {"username": "portal", "follower_count": 77777, "pk": "1"}}})
    r = mon.coletar_novos(con, coletor, mon.monitoradas(con), aviso=lambda _: None, com_stories=False)
    assert cliente.pedidos == [("perfil", "portal")]  # uma visita só
    assert r["perfis"] == 1
    assert con.execute("SELECT seguidores FROM contas WHERE username = 'portal'").fetchone()[0] == 77777


def test_ultimo_post_ignora_stories(con, municipios):
    conta(con, municipios, "portal", [post(2, codigo="V1")])
    con.execute("INSERT INTO posts (rede, post_id, username, tipo, publicado_em, medido_em) "
                "VALUES ('instagram', 'st1', 'portal', 'Story', ?, 'x')",
                (datetime.now(timezone.utc).isoformat(),))
    coletor = ApifyFalso({config.ATOR_POSTS: [], config.ATOR_PERFIL: []})
    agora = datetime.now(timezone.utc)
    mon.coletar_novos(con, coletor, mon.monitoradas(con), aviso=lambda _: None, agora=agora, com_stories=False)
    entrada = coletor.chamadas[0][1]
    # Desde o último post da grade (2 dias), não desde o story de agora; e a primeira página cobre os 3 dias.
    assert entrada["desde_por_conta"]["portal"][:10] == (agora - timedelta(days=2)).date().isoformat()
    assert entrada["atualizar_desde"][:10] == (agora - mon.JANELA_ATUALIZACAO).date().isoformat()


def test_ritmo_uniforme_espalha_as_leituras_pelo_dia(monkeypatch):
    monkeypatch.setattr(config, "NAV_MAX_HORA", 100)
    monkeypatch.setattr(config, "NAV_MAX_DIA", 800)
    agora = [1_000_000.0]
    pausas = []

    def dormir(segundos):
        pausas.append(segundos)
        agora[0] += segundos

    ritmo = nav.Ritmo(None, dormir, lambda: agora[0], uniforme=True)
    for _ in range(3):
        ritmo.esperar()
    # Dia sem leituras: 800 em 12 h = ~67 por hora, ~54 s entre leituras (com variação de 30%).
    assert ritmo.intervalo_uniforme() == pytest.approx(3600 / ((800 - 3) / 12))
    assert all(0.7 * 50 <= p <= 1.3 * 56 for p in pausas)
    # Perto do teto, não cai abaixo do ritmo de um dia inteiro (800 em 24 h).
    ritmo.leituras = [agora[0]] * 790
    assert ritmo.intervalo_uniforme() == pytest.approx(3600 / (800 / 24))
