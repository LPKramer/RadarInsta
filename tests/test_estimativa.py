"""Interações estimadas dos stories (estimativa.py): a mediana dos posts de feed mais próximos, com teto nos
seguidores, e o número no painel, na soma da mesma notícia e no relatório."""
from datetime import datetime, timedelta, timezone

import pytest

from radar import capas, db, estimativa, fatos, painel, relatorio

AGORA = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture(autouse=True)
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(capas, "PASTA", tmp_path / "capas")
    monkeypatch.setattr(capas, "STORIES", tmp_path / "stories")


def _quando(dias: float) -> str:
    return (AGORA + timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def feed(con, username, post_id, dias, interacoes, tipo="Image"):
    con.execute("INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, curtidas, comentarios, "
                "medido_em) VALUES ('instagram', ?, ?, ?, ?, ?, ?, 0, 'x')",
                (post_id, username, f"https://exemplo/{post_id}", tipo, _quando(dias), interacoes))


def story(con, username, post_id, dias, resumo="Bairro sem energia desde a manhã."):
    con.execute("INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, medido_em) VALUES "
                "('instagram', ?, ?, ?, 'Story', ?, 'x')", (post_id, username, f"https://exemplo/{post_id}",
                                                            _quando(dias)))
    mencao(con, post_id, resumo)


def mencao(con, post_id, resumo="Bairro sem energia desde a manhã."):
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, tema, sentimento, alcance, resumo, "
                "analisada_em) VALUES ('instagram', ?, 1, 0, 'falta_energia', 'negativo', 'local', ?, 'x')",
                (post_id, resumo))


def test_mediana_dos_vizinhos_teto_e_taxa_padrao(con):
    db.upsert_conta(con, {"username": "portal", "seguidores": 10_000, "origem": "t"})
    for i in range(10):  # a vizinhança: 200 por post, antes e depois do story
        feed(con, "portal", f"p{i}", -5 + i, 200)
    feed(con, "portal", "viral", -1, 9_000)  # o viral isolado não puxa a mediana
    for i in range(5):  # posts de um mês antes: longe no tempo, quase todos ficam fora
        feed(con, "portal", f"velho{i}", -40 - i, 5)
    feed(con, "portal", "outro_story", 0, 99_999, tipo="Story")  # story não serve de vizinho
    assert estimativa.estimar(con, "portal", _quando(0), 10_000) == 200

    db.upsert_conta(con, {"username": "pequena", "seguidores": 1_000, "origem": "t"})
    for i in range(5):
        feed(con, "pequena", f"q{i}", -i, 300)
    assert estimativa.estimar(con, "pequena", _quando(0), 1_000) == 50  # clipado em 5% dos seguidores
    assert estimativa.estimar(con, "nova", _quando(0), 20_000) == 140  # sem posts: 0,7% dos seguidores
    assert estimativa.estimar(con, "nova", _quando(0), None) is None


def test_story_estimado_no_painel_na_soma_da_noticia_e_no_relatorio(con):
    db.upsert_conta(con, {"username": "portal", "seguidores": 10_000, "origem": "t"})
    db.upsert_conta(con, {"username": "outro", "seguidores": 50_000, "origem": "t"})
    for i in range(6):
        feed(con, "portal", f"p{i}", -i, 300)
    story(con, "portal", "s1", -0.1)
    feed(con, "outro", "o1", -0.2, 1_000)
    mencao(con, "o1")
    con.commit()
    assert estimativa.atualizar(con) == 1 and estimativa.atualizar(con) == 0  # a segunda vez não muda nada

    agora = AGORA + timedelta(hours=1)
    stories, _ = painel.posts(con, {"formato": "Story"}, agora=agora)
    assert (stories[0]["interacoes"], stories[0]["estimado"]) == (300, True)
    ficha = painel.post(con, "s1", agora=agora)
    assert ficha["engajamentoPct"] is None and ficha["indice"] is None  # a estimativa já é a mediana da conta
    assert painel.post(con, "o1", agora=agora)["estimado"] is False

    # A mesma notícia soma o story pela estimativa.
    con.execute("INSERT INTO fatos (id, titulo, criado_em, atualizado_em) VALUES (1, 'Bairro sem energia', 'x', 'x')")
    for post_id in ("s1", "o1"):
        con.execute("INSERT INTO fato_mencao (rede, post_id, fato_id, como, ligado_em) VALUES "
                    "('instagram', ?, 1, 'novo', 'x')", (post_id,))
    con.commit()
    resumo = fatos.resumo(con, 1)
    assert (resumo["interacoes"], resumo["interacoesEstimadas"]) == (1_300, 300)
    df = relatorio.carregar(con).set_index("link")
    linha = df.loc["https://exemplo/s1"]
    assert (linha["interacoes"], linha["interacoes_estimadas"], linha["fato_interacoes"]) == (300, 300, 1_300)
    assert linha["engajamento_pct"] != linha["engajamento_pct"]  # NaN: sem taxa para a estimativa
