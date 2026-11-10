import io
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from radar import capas, db, navegador as nav, perfis

from conftest import conta, post


def _url(nome: str, horas: float = 48) -> str:
    """Endereço no formato do Instagram, com o prazo (oe, em hexadecimal) daqui a `horas`."""
    prazo = int((datetime.now(timezone.utc) + timedelta(hours=horas)).timestamp())
    return f"https://cdn.exemplo/{nome}.jpg?stp=dst-jpg&oe={prazo:X}&_nc_sid=1"


def _jpeg(largura: int = 1080, altura: int = 1350) -> bytes:
    saida = io.BytesIO()
    Image.new("RGB", (largura, altura), (200, 40, 40)).save(saida, "JPEG")
    return saida.getvalue()


@pytest.fixture(autouse=True)
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(capas, "PASTA", tmp_path / "capas")
    monkeypatch.setattr(capas, "STORIES", tmp_path / "stories")
    return tmp_path / "capas"


def _story(con, post_id: str, energisa: bool, arquivo=None) -> None:
    con.execute("INSERT INTO posts (rede, post_id, username, tipo, publicado_em, medido_em, arquivo) "
                "VALUES ('instagram', ?, 'portal', 'Story', '2026-11-07T10:00:00', 'x', ?)",
                (post_id, str(arquivo) if arquivo else None))
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, analisada_em) "
                "VALUES ('instagram', ?, ?, 0, 'x')", (post_id, int(energisa)))


def test_melhor_imagem_escolhe_a_menor_que_cobre_a_largura():
    candidatos = [{"width": w, "url": f"u{w}"} for w in (1080, 640, 480, 320, 150)]
    assert capas.melhor_imagem({"image_versions2": {"candidates": candidatos}}) == "u480"
    assert capas.melhor_imagem({"image_versions2": {"candidates": candidatos[3:]}}) == "u320"  # só pequenas: a maior
    carrossel = {"image_versions2": None, "carousel_media": [{"image_versions2": {"candidates": candidatos[:2]}}]}
    assert capas.melhor_imagem(carrossel) == "u640"
    assert capas.melhor_imagem({"display_uri": "https://cdn/x.jpg"}) == "https://cdn/x.jpg"
    assert capas.melhor_imagem({"display_uri": None}) is None


def test_validade_do_endereco_vem_do_parametro_oe():
    assert capas.valida(_url("a", horas=10))
    assert not capas.valida(_url("a", horas=-1))
    assert capas.valida("https://cdn/sem-prazo.jpg")
    assert not capas.valida(None) and not capas.valida("javascript:alert(1)")


def test_guardar_reduz_e_recusa_o_que_nao_e_imagem(pasta):
    destino = pasta / "posts" / "1.jpg"
    assert capas.guardar(_jpeg(), destino, 480)
    with Image.open(destino) as imagem:
        assert imagem.size == (480, 600)
    assert not capas.guardar(b"<html>expirou</html>", pasta / "posts" / "2.jpg", 480)
    assert not (pasta / "posts" / "2.jpg").exists()
    assert not list((pasta / "posts").glob("*.tmp"))


def test_arquivos_so_aceitam_ids_e_usernames_validos(pasta):
    assert capas.arquivo_post("../../segredo") is None
    assert capas.arquivo_foto("..\\x") is None
    assert capas.arquivo_post("3857108401835109949") == pasta / "posts" / "3857108401835109949.jpg"


def test_coletor_guarda_o_endereco_da_capa_e_da_foto(con, municipios):
    no = {"pk": "9", "code": "C9", "media_type": 2, "taken_at": 1761955200, "like_count": 1, "comment_count": 0,
          "image_versions2": {"candidates": [{"width": 640, "url": _url("capa")}]}}
    assert nav.post_do_site(no)["displayUrl"].startswith("https://cdn.exemplo/capa.jpg")
    usuario = {"pk": "1", "username": "Portal", "follower_count": 10, "profile_pic_url": "https://cdn/p.jpg",
               "hd_profile_pic_url_info": {"url": "https://cdn/p_hd.jpg"}}
    assert nav.item_de_perfil(usuario, [no], [])["profilePicUrl"] == "https://cdn/p_hd.jpg"

    primeiro = post(1, codigo="P1") | {"displayUrl": _url("v1")}
    conta(con, municipios, "portal", [primeiro])
    perfis.ingerir(con, {"username": "portal", "followersCount": 10, "profilePicUrl": "https://cdn/p.jpg"},
                   "monitor", municipios)
    assert con.execute("SELECT foto FROM contas WHERE username = 'portal'").fetchone()[0] == "https://cdn/p.jpg"
    # Uma visita nova traz o endereço renovado; uma sem endereço não apaga o que havia.
    perfis.ingerir_posts(con, "portal", [primeiro | {"displayUrl": _url("v2")}])
    assert "v2.jpg" in con.execute("SELECT capa FROM posts WHERE post_id = 'id-P1'").fetchone()[0]
    perfis.ingerir_posts(con, "portal", [{k: v for k, v in primeiro.items() if k != "displayUrl"}])
    assert "v2.jpg" in con.execute("SELECT capa FROM posts WHERE post_id = 'id-P1'").fetchone()[0]


def test_rodada_baixa_pendentes_brutos_e_nao_insiste_nas_falhas(con, municipios, tmp_path, pasta):
    conta(con, municipios, "portal", [post(1, codigo="A") | {"displayUrl": _url("a")},
                                      post(2, codigo="B") | {"displayUrl": _url("b", horas=-2)},  # vencido
                                      post(3, codigo="C") | {"displayUrl": _url("quebrado")}])
    con.execute("UPDATE contas SET foto = ? WHERE username = 'portal'", (_url("foto"),))
    for post_id in ("id-A", "id-B", "id-C"):  # só as menções à Energisa ganham capa
        con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, analisada_em) "
                    "VALUES ('instagram', ?, 1, 0, 'x')", (post_id,))
    # Stories: 555 é da Energisa e tem a imagem salva para o OCR (vídeo, com a .jpg ao lado); 777 é da Energisa
    # mas só tem o endereço nas cópias brutas; 888 não é da Energisa e fica sem capa.
    salva = capas.STORIES / "portal" / "555.jpg"
    salva.parent.mkdir(parents=True)
    salva.write_bytes(_jpeg(720, 1280))
    _story(con, "555", True, salva.with_suffix(".mp4"))
    _story(con, "777", True)
    _story(con, "888", False)
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "20261107-104805_stories_nav-x.json").write_text(json.dumps(
        {"itens": [{"id": "777", "imagem": _url("story")}, {"id": "888", "imagem": _url("outro")}]}),
        encoding="utf-8")
    pedidos = []

    def baixar(url):
        pedidos.append(url)
        return None if "quebrado" in url else _jpeg(320, 320)

    falhas = set()
    feitos = capas.rodada(con, raw_dir=raw, baixar_url=baixar, dormir=lambda _: None, falhas=falhas)
    assert feitos == {"baixadas": 4, "falhas": 1, "faltam": 0}
    assert {p.name for p in (pasta / "posts").iterdir()} == {"id-A.jpg", "555.jpg", "777.jpg"}
    assert (pasta / "perfis" / "portal.jpg").exists()
    assert not any("b.jpg" in u for u in pedidos)  # o vencido nem é pedido
    assert not any("outro.jpg" in u or "555" in u for u in pedidos)  # nem o story de fora, nem o que tem imagem
    pedidos.clear()
    assert capas.rodada(con, raw_dir=raw, baixar_url=baixar, dormir=lambda _: None, falhas=falhas)["baixadas"] == 0
    assert pedidos == []  # nada novo, e o que falhou não é pedido de novo


def test_limpar_apaga_as_capas_de_tudo_que_nao_e_mencao(con, pasta):
    (pasta / "posts").mkdir(parents=True)
    for nome in ("velha", "nova", "mencao", "story-fora", "story-energisa"):
        (pasta / "posts" / f"{nome}.jpg").write_bytes(_jpeg(10, 10))
    antigo = time.time() - 90 * 86400
    os.utime(pasta / "posts" / "mencao.jpg", (antigo, antigo))  # a da menção fica, por mais velha que seja
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, analisada_em) "
                "VALUES ('instagram', 'mencao', 1, 0, 'x')")
    _story(con, "story-fora", False)
    _story(con, "story-energisa", True)
    assert capas.limpar(con) == 3  # a velha, a nova e o story fora da Energisa: o painel não os mostra
    assert {p.stem for p in (pasta / "posts").iterdir()} == {"mencao", "story-energisa"}


def test_banco_sem_as_colunas_novas_nao_quebra_os_pendentes(tmp_path):
    import sqlite3

    antigo = sqlite3.connect(tmp_path / "antigo.db")
    antigo.execute("CREATE TABLE posts (rede, post_id, username, shortcode, publicado_em)")
    antigo.execute("CREATE TABLE contas (rede, username, seguidores)")
    assert capas.pendentes(antigo) == []
    antigo.close()
    novo = db.conectar(tmp_path / "antigo.db")  # a migração cria as colunas
    assert "capa" in {l["name"] for l in novo.execute("PRAGMA table_info(posts)")}
    assert "foto" in {l["name"] for l in novo.execute("PRAGMA table_info(contas)")}
    novo.close()


def test_visita_guarda_o_endereco_da_foto_do_perfil(con, municipios, tmp_path):
    from radar import monitoramento as mon
    from test_navegador import navegador as navegador_falso, no, visita

    conta(con, municipios, "portal", [post(10, codigo="V1")])  # perfil lido agora: a releitura da semana não roda
    coletor, _ = navegador_falso(tmp_path, visitas={"portal": visita([no(1, 0.1)], False) | {
        "user": {"username": "portal", "follower_count": 9, "pk": "1", "profile_pic_url": _url("perfil")}}})
    mon.coletar_novos(con, coletor, mon.monitoradas(con), aviso=lambda _: None, com_stories=False)
    assert "perfil.jpg" in con.execute("SELECT foto FROM contas WHERE username = 'portal'").fetchone()[0]
