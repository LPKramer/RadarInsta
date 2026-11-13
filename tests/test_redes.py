"""Threads e X: a busca, a investigação e a análise por rede (Fase 3.1). Os itens já chegam no formato da Apify, como
os leitores de redes.py devolvem; os dados são fictícios."""
import sqlite3
from datetime import datetime, timedelta, timezone

from radar import busca_posts as bp, config, db, investigacao, mencoes, navegador as nav, redes, vigia

from conftest import deepseek_falso, resposta_deepseek

AGORA = datetime.now(timezone.utc)


def item(post_id, dono, texto, horas=2, **extra) -> dict:
    """Post do Threads ou do X no formato da Apify, com o prefixo da rede no id."""
    return {"id": post_id, "url": f"https://exemplo/{post_id}", "type": "Text", "caption": texto,
            "timestamp": (AGORA - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "likesCount": 4,
            "commentsCount": 1, "ownerUsername": dono, "ownerFullName": dono.title()} | extra


class Coletor:
    def __init__(self, itens):
        self.itens, self.pedidos = itens, []

    def buscar_rede(self, rede, termo):
        self.pedidos.append((rede, termo))
        return self.itens

    def perfil_rede(self, rede, username):
        raise AssertionError("a herança não lê perfil")


def test_busca_no_x_grava_na_rede_certa_com_as_pistas_do_perfil(con, municipios):
    coletor = Coletor([
        item("x_1", "maria_to", "Acabou a energia de novo aqui em Palmas, Energisa!"),
        item("x_2", "joao", "Sem energia desde ontem, ninguém da Energisa aparece",
             ownerBiography="Professor", ownerLocation="Gurupi - TO"),
        item("x_3", "loja", "Promoção de ventiladores"),
    ])
    resumo = bp.buscar(con, coletor, bp.TERMOS_X[0], AGORA, rede="x")
    assert coletor.pedidos == [("x", bp.TERMOS_X[0])]
    assert (resumo["do_to"], resumo["a_investigar"], resumo["sem_energia"]) == (1, 1, 1)
    linhas = {l[0]: tuple(l)[1:] for l in con.execute("SELECT post_id, rede, estado FROM posts_busca")}
    assert linhas == {"x_1": ("x", "do_to"), "x_2": ("x", "a_investigar")}
    assert con.execute("SELECT rede FROM posts WHERE post_id = 'x_1'").fetchone()[0] == "x"
    assert tuple(con.execute("SELECT bio, local_perfil FROM contas WHERE rede = 'x' AND username = 'joao'"
                             ).fetchone()) == ("Professor", "Gurupi - TO")
    # Os termos e o relógio de cada rede são separados: o Instagram continua com tudo vencido.
    assert bp.TERMOS_X[0] not in bp.vencidos(con, AGORA, rede="x") and bp.vencidos(con, AGORA) == bp.TERMOS
    assert bp.situacao(con, "x")["posts_achados"] == 2 and bp.situacao(con)["posts_achados"] == 0


def test_no_x_a_bio_e_o_local_ja_entram_na_etapa_1(con, municipios):
    bp.ingerir(con, "energisa", [item("x_2", "joao", "Sem energia desde ontem", ownerLocation="Gurupi - TO")],
               municipios, AGORA, rede="x")
    [autor] = investigacao.pendentes(con, rede="x")
    texto = investigacao.descrever(autor, municipios)
    assert "@joao (Joao) no X" in texto and "Local escrito no perfil: Gurupi - TO" in texto
    assert "Pistas de texto (cidades do TO citadas): Gurupi" in texto
    assert investigacao.pendentes(con) == []  # a fila do Instagram não mistura


def test_threads_herda_o_veredito_do_instagram_sem_ia(con, municipios):
    palmas = next(m["ibge_id"] for m in municipios if m["nome"] == "Palmas")
    con.execute("INSERT INTO investigacoes (rede, username, cidade, uf, ibge_id, do_to, confianca, evidencias, etapa, "
                "modelo, investigada_em) VALUES ('instagram', 'maria', 'Palmas', 'TO', ?, 1, 0.9, 'DDD 63', 1, 'm', ?)",
                (palmas, db.agora()))
    bp.ingerir(con, "energisa", [item("th_1", "maria", "Faltou energia de novo"),
                                 item("th_2", "fulano", "Faltou energia de novo")], municipios, AGORA, rede="threads")
    pedidos = []

    def responder(params):
        pedidos.append(params)
        return resposta_deepseek({"cidade": None, "uf": None, "confianca": 0.1, "evidencias": ""})

    cliente, _ = deepseek_falso(responder)
    autores = investigacao.pendentes(con, rede="threads")
    resumo = investigacao.investigar(con, cliente, None, autores, aviso=lambda _: None, rede="threads")
    assert resumo["herdados"] == 1 and len(pedidos) == 1  # só o fulano foi à IA
    linha = con.execute("SELECT cidade, do_to, etapa FROM investigacoes WHERE rede = 'threads' AND username = 'maria'"
                        ).fetchone()
    assert tuple(linha) == ("Palmas", 1, 0)
    assert con.execute("SELECT estado FROM posts_busca WHERE post_id = 'th_1'").fetchone()[0] == "do_to"


def test_analise_e_alerta_dizem_a_rede(con, municipios):
    bp.ingerir(con, "energisa", [item("x_9", "maria_to", "Acabou a energia em Palmas, Energisa!")], municipios, AGORA,
               rede="x")
    con.execute("INSERT INTO investigacoes (rede, username, cidade, uf, do_to, confianca, etapa, investigada_em) "
                "VALUES ('x', 'maria_to', 'Palmas', 'TO', 1, 0.9, 1, ?)", (db.agora(),))
    [post] = [p for p in bp.candidatos(con) if p["rede"] == "x"]
    texto = mencoes.descrever(post)
    assert "@maria_to no X (localização provável: Palmas-TO)" in texto
    assert mencoes.FORMATO_REDE.format(rede="X") in texto and "Texto: Acabou a energia" in texto
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, tema, setor, sentimento, alcance, "
                "resumo, confianca, modelo, analisada_em) VALUES ('x', 'x_9', 1, 1, 'falta_energia', 'operacao', "
                "'negativo', 'local', 'Falta de energia em Palmas.', 0.9, 'm', ?)", (db.agora(),))
    assert vigia.alertar(con, [post], aviso=lambda _: None) == 1
    assert "  X @maria_to  " in vigia.ARQUIVO_ALERTAS.read_text(encoding="utf-8")


def _cookie(pasta, host, nome):
    arquivo = pasta / "Default" / "Network" / "Cookies"
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    banco = sqlite3.connect(arquivo)
    banco.execute("CREATE TABLE IF NOT EXISTS cookies (host_key TEXT, name TEXT, encrypted_value BLOB)")
    banco.execute("INSERT INTO cookies VALUES (?, ?, x'00')", (host, nome))
    banco.commit()
    banco.close()


def test_conta_de_outra_rede_fica_fora_do_instagram(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NAVEGADOR_DIR", tmp_path)
    _cookie(tmp_path / "perfil-conta2", ".instagram.com", "sessionid")

    def edge_falso(pasta, url, espera, aviso):  # a pessoa entra na janela e fecha
        if "threads" in url:
            _cookie(pasta, ".threads.com", "sessionid")
            _cookie(pasta, ".instagram.com", "sessionid")  # o login do Threads pode deixar o do Instagram
        else:
            _cookie(pasta, ".x.com", "auth_token")
        return True

    monkeypatch.setattr(nav, "_edge_comum", edge_falso)
    assert nav.entrar(lambda _: None, conta="conta7", rede="threads")
    assert nav.entrar(lambda _: None, conta="x1", rede="x")
    assert nav.entrar(lambda _: None, conta="conta2", rede="threads")  # conta que já era do Instagram
    assert (nav.redes_da_conta("conta7"), nav.redes_da_conta("x1")) == ({"threads"}, {"x"})
    assert nav.redes_da_conta("conta2") == {"instagram", "threads"}
    assert nav.tem_sessao(nav.pasta_conta("x1"), "x") is True and nav.tem_sessao(nav.pasta_conta("x1")) is False
    # A vigilância do Instagram só pega as contas do Instagram, mesmo com o cookie de lá.
    assert vigia.contas_novas(set()) == ["conta2"]
    assert redes.REDES["x"].url_busca("energisa palmas").endswith("q=energisa%20palmas&src=typed_query&f=live")
    assert redes.REDES["threads"].url_perfil("maria") == "https://www.threads.com/@maria"
