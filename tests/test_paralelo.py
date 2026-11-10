import pytest

from radar import db, navegador as nav, paralelo

from conftest import conta
from test_stories import ColetorFalso, item_story


def test_dois_trabalhadores_dividem_a_fila_sem_repetir(tmp_path):
    caminho = tmp_path / "radar.db"
    a, b = db.conectar(caminho), db.conectar(caminho)  # duas conexões, como dois processos
    usernames = [f"conta{i:02d}" for i in range(25)]
    assert paralelo.preparar(a, "r1", usernames) == 25
    vistos = []
    while True:
        lote_a, lote_b = paralelo.pegar_lote(a, "r1", "a"), paralelo.pegar_lote(b, "r1", "b")
        if not lote_a and not lote_b:
            break
        vistos += lote_a + lote_b
        paralelo.concluir(a, "r1", lote_a)
        paralelo.concluir(b, "r1", lote_b)
    assert sorted(vistos) == usernames  # todas, nenhuma duas vezes
    assert paralelo.situacao(a, "r1") == {"feita": 25}
    assert paralelo.preparar(a, "r1", usernames) == 0  # rodar de novo no mesmo dia não refaz


def test_lote_interrompido_volta_para_a_fila(con):
    paralelo.preparar(con, "r1", ["a", "b", "c"])
    lote = paralelo.pegar_lote(con, "r1", "principal", tamanho=2)
    assert paralelo.situacao(con, "r1") == {"em_andamento": 2, "pendente": 1}
    # Um trabalhador que morreu no meio deixa o lote "em andamento": a próxima rodada o devolve à fila.
    assert paralelo.preparar(con, "r1", ["a", "b", "c"]) == 3
    paralelo.devolver(con, "r1", lote)
    assert paralelo.situacao(con, "r1") == {"pendente": 3}


def test_trabalhador_le_stories_da_fila(con, municipios, tmp_path, monkeypatch):
    from radar import stories

    monkeypatch.setattr(stories, "PASTA", tmp_path)
    for username in ("portal", "outro"):
        conta(con, municipios, username, [])
    paralelo.preparar(con, "r1", ["portal", "outro"])
    resumo = paralelo.processar(con, ColetorFalso([item_story(1), item_story(2)]), "r1", "stories", "principal",
                                aviso=lambda _: None)
    assert resumo["contas"] == 2 and resumo["stories"] == 4
    assert paralelo.situacao(con, "r1") == {"feita": 2}


def test_interrupcao_devolve_o_lote_e_para(con, municipios):
    class Bloqueado(ColetorFalso):
        def stories(self, username):
            raise nav.ColetaInterrompida("O Instagram pediu uma pausa")

    conta(con, municipios, "portal", [])
    paralelo.preparar(con, "r1", ["portal"])
    with pytest.raises(nav.ColetaInterrompida):
        paralelo.processar(con, Bloqueado([]), "r1", "stories", "principal", aviso=lambda _: None)
    assert paralelo.situacao(con, "r1") == {"pendente": 1}  # outra conta pega


def test_cada_conta_tem_seu_perfil_e_seu_ritmo(tmp_path):
    for nome in ("perfil", "perfil-b", "perfil-a", "outra-pasta"):
        (tmp_path / nome).mkdir()
    assert nav.contas_configuradas(tmp_path) == ["principal", "a", "b"]
    assert nav.pasta_conta("principal") == nav.PASTA_PERFIL
    assert nav.pasta_conta("b").name == "perfil-b" and nav.arquivo_leituras("b").name == "leituras-b.json"
    with pytest.raises(ValueError):
        nav.pasta_conta("../fora")
