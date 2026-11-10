import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from radar import capas, config, imagens

from conftest import ApifyFalso, conta, perfil, post


@pytest.fixture(autouse=True)
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(capas, "PASTA", tmp_path / "capas")
    return tmp_path / "capas"


def _url(nome: str, horas: float = 48) -> str:
    prazo = int((datetime.now(timezone.utc) + timedelta(hours=horas)).timestamp())
    return f"https://cdn.exemplo/{nome}.jpg?oe={prazo:X}"


def _jpeg() -> bytes:
    saida = io.BytesIO()
    Image.new("RGB", (600, 600), (0, 159, 194)).save(saida, "JPEG")
    return saida.getvalue()


def test_busca_so_o_que_falta_e_baixa_fotos_e_capas_das_mencoes(con, municipios, pasta, monkeypatch):
    conta(con, municipios, "portal", [post(9, codigo="M1", legenda="Apagão em Gurupi")])
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, analisada_em) "
                "VALUES ('instagram', 'id-M1', 1, 0, 'x')")
    assert [c["username"] for c in imagens.contas_sem_foto(con)] == ["portal"]
    assert [m["post_id"] for m in imagens.mencoes_sem_capa(con)] == ["id-M1"]
    assert imagens.custo(1000, 0) == pytest.approx(imagens.PRECO_PERFIL_1K)

    # O perfil traz a foto e só os posts recentes (a menção de 9 dias atrás fica de fora): ela vem pelo link.
    apify = ApifyFalso({
        config.ATOR_PERFIL: [perfil("portal", profilePicUrl=_url("foto"),
                                    latestPosts=[post(0, codigo="N1") | {"displayUrl": _url("n1")}])],
        imagens.ATOR_LINKS: [post(9, codigo="M1", legenda="Apagão em Gurupi") | {"ownerUsername": "portal",
                                                                                  "displayUrl": _url("m1")}],
    })
    resumo = imagens.buscar(con, apify, imagens.contas_sem_foto(con), imagens.mencoes_sem_capa(con),
                            aviso=lambda _: None)
    assert resumo == {"perfis": 1, "posts": 1}
    assert apify.chamadas[1] == (imagens.ATOR_LINKS, {"directUrls": ["https://www.instagram.com/p/M1/"],
                                                      "resultsType": "posts", "resultsLimit": 1,
                                                      "addParentData": False})

    pedidos = []
    monkeypatch.setattr(capas, "baixar", lambda url, timeout=15: pedidos.append(url) or _jpeg())
    feitos = imagens.baixar_agora(con, [{"post_id": "id-M1"}], aviso=lambda _: None)
    assert feitos == {"fotos": 1, "capas": 1, "falhas": 0}
    assert (pasta / "perfis" / "portal.jpg").exists() and (pasta / "posts" / "id-M1.jpg").exists()
    assert not any("n1.jpg" in u for u in pedidos)  # a capa do post comum fica para o painel, em segundo plano
    assert imagens.contas_sem_foto(con) == [] and imagens.mencoes_sem_capa(con) == []


def test_pendentes_so_capas_de_mencoes_e_fotos_das_contas_do_painel(con, municipios):
    conta(con, municipios, "portal", [post(1, codigo="A") | {"displayUrl": _url("a")},
                                      post(2, codigo="B") | {"displayUrl": _url("b")}], seguidores=30_000)
    conta(con, municipios, "outra", [post(1, codigo="C") | {"displayUrl": _url("c")}], seguidores=20_000)
    conta(con, municipios, "fora", [post(1, codigo="D")], prioridade="C")  # nem monitorada nem fala da Energisa
    con.execute("UPDATE contas SET foto = ?", (_url("foto"),))
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, analisada_em) "
                "VALUES ('instagram', 'id-A', 1, 0, 'x')")
    # A capa da menção e as fotos das monitoradas (a página Contas mostra todas); nada do post comum nem da "fora".
    assert [(d.parent.name, d.stem) for d, _, _ in capas.pendentes(con)] == [
        ("posts", "id-A"), ("perfis", "portal"), ("perfis", "outra")]
    assert [c["username"] for c in imagens.contas_sem_foto(con)] == []  # as fotos ainda valem
