import re
from datetime import datetime, timedelta, timezone

import pytest

from radar import capas, db, fatos, painel, relatorio

from conftest import deepseek_falso, resposta_deepseek

INICIO = datetime(2026, 11, 9, 19, 0, tzinfo=timezone.utc)
G1 = "https://g1.globo.com/to/tocantins/noticia/2026/11/09/estudante-morre-apos-choque-no-ifto.ghtml"


@pytest.fixture(autouse=True)
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(capas, "PASTA", tmp_path / "capas")
    monkeypatch.setattr(capas, "STORIES", tmp_path / "stories")


def _ibge(municipios, nome):
    return next(m["ibge_id"] for m in municipios if m["nome"] == nome)


def mencao(con, post_id, username, minutos, resumo, municipio=None, rede="instagram", tipo="Image", legenda="",
           tema="acidente_seguranca", url_externa=None, curtidas=100, comentarios=10, sentimento="negativo"):
    db.upsert_conta(con, {"username": username, "url_externa": url_externa, "origem": "teste"}, rede)
    quando = (INICIO + timedelta(minutes=minutos)).isoformat(timespec="seconds")
    con.execute("INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, curtidas, comentarios, "
                "medido_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rede, post_id, username, f"https://exemplo/{post_id}", tipo, quando, legenda or resumo,
                 curtidas, comentarios, quando))
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, tema, sentimento, alcance, resumo, "
                "confianca, modelo, analisada_em) VALUES (?, ?, 1, 0, ?, ?, 'local', ?, 0.9, 'm', ?)",
                (rede, post_id, tema, sentimento, resumo, quando))
    if municipio:
        con.execute("INSERT INTO mencao_municipio (rede, post_id, ibge_id) VALUES (?, ?, ?)", (rede, post_id, municipio))
    con.commit()


def ia(decidir=lambda texto: "A"):
    """Responde à consolidação: para cada menção [n] do lote, a letra que `decidir(texto do item)` devolver."""
    def responder(params):
        texto = params["messages"][1]["content"]
        itens = re.split(r"\n\n(?=\[\d+\])", texto)
        return resposta_deepseek({str(i): decidir(item) for i, item in enumerate(itens, 1)})
    return deepseek_falso(responder)


def test_links_e_veiculos(con):
    assert fatos.normalizar_link(G1 + "?utm_source=ig&id=7") == G1.removeprefix("https://") + "?id=7"
    assert fatos.normalizar_link("https://portaldoamaral.com.br/ultimas-noticias/") is None  # seção, não matéria
    assert fatos.normalizar_link("https://wa.me/5563999999999") is None
    assert fatos.links(f"Estudante morre\nLink: {G1.replace('https://', 'https://www.')}/") == {
        G1.removeprefix("https://")}
    for site in ("afnoticias.com.br", "g1.globo.com", "tonoticia.com.br"):
        db.upsert_conta(con, {"username": site, "origem": "t"}, "web")
    db.upsert_conta(con, {"username": "afnoticias", "url_externa": "https://www.afnoticias.com.br/", "origem": "t"})
    db.upsert_conta(con, {"username": "jornalismoamazonica", "url_externa": "https://g1.globo.com/rr/noticia/x",
                           "origem": "t"})
    db.upsert_conta(con, {"username": "tonoticia_oficial", "origem": "t"})
    veiculo = fatos.Veiculos(con)
    assert veiculo("web", "afnoticias.com.br") == veiculo("instagram", "afnoticias") == "afnoticias.com.br"
    assert veiculo("instagram", "jornalismoamazonica") == "ig:jornalismoamazonica"  # link profundo não é o site
    assert veiculo("instagram", "tonoticia_oficial") == "tonoticia.com.br"


def test_a_mesma_noticia_vira_um_agrupamento_com_as_somas(con, municipios):
    diano = _ibge(municipios, "Dianópolis")
    mencao(con, "1", "akontecenews", 0, "Aluno morreu após sofrer choque elétrico na quadra do IFTO em Dianópolis.",
           diano)
    mencao(con, "2", "akontecenews", 5, "Aluno morreu após sofrer choque elétrico na quadra do IFTO em Dianópolis.",
           diano, tipo="Story", curtidas=None, comentarios=None)
    mencao(con, "3", "portaltaguatinga24h", 10, "Estudante morreu após sofrer choque elétrico no IFTO em Dianópolis.",
           diano, curtidas=300, comentarios=40)
    mencao(con, "4", "afnoticias", 30, "Estudante do IFTO morreu após choque elétrico na quadra em Dianópolis.", diano,
           url_externa="https://afnoticias.com.br", curtidas=500, comentarios=60)
    mencao(con, "w1", "afnoticias.com.br", 40, "Estudante do IFTO morre após choque elétrico em Dianópolis.", diano,
           rede="web", tipo="Materia", curtidas=None, comentarios=None)
    # Outro fato, no mesmo dia: nada parecido, vira um agrupamento sozinho, sem perguntar à IA.
    mencao(con, "5", "palmasagora", 20, "Energisa anuncia mutirão para negociar contas atrasadas em Palmas.",
           tema="conta_tarifa", sentimento="positivo")
    cliente, chamadas = ia()
    resumo = fatos.consolidar(con, cliente, aviso=lambda _: None)
    assert resumo["novos"] == 2 and resumo["ia_juntou"] == 4 and len(chamadas) == 4
    fato = fatos.ficha(con, "instagram", "3")
    assert fato["posts"] == 5 and fato["perfis"] == 3  # o site e o perfil da AF Notícias contam uma vez
    assert (fato["curtidas"], fato["comentarios"], fato["interacoes"]) == (900, 110, 1010)  # story e matéria: sem
    assert fato["primeira"]["conta"] == "akontecenews" and fato["redes"] == ["instagram", "web"]
    assert fatos.ficha(con, "instagram", "5") is None  # sozinho: não há "mesma notícia"
    assert fatos.pendentes(con) == []
    assert "@akontecenews" in chamadas[-1]["messages"][1]["content"]  # a IA vê de quem é cada candidata

    # O painel mostra o agrupamento na ficha, e a repercussão passa a ser a dele.
    ficha = painel.post(con, "3")
    assert ficha["fato"]["perfis"] == 3 and ficha["mencao"]["repercutiram"] == 3
    assert sorted(p["id"] for p in ficha["mesmaNoticia"]) == ["1", "2", "4", "w1"]
    # O Top 10 e o destaque contam notícias: o IFTO ocupa um lugar só, no post de mais interações, com a soma.
    top, _ = painel.top10(con, {"ordem": "interacoes", "sem_stories": 1, "rede": "instagram"}, 10)
    assert [p["id"] for p in top] == ["4", "5"] and "noticia" not in top[1]
    assert (top[0]["noticia"]["perfis"], top[0]["noticia"]["interacoes"]) == (3, 1010)
    assert [p["id"] for p in painel.destaques(con, agora=INICIO + timedelta(days=1))] == ["4", "5"]
    df = relatorio.carregar(con).set_index("post_id")
    assert df.loc["3", "fato_posts"] == 5 and df.loc["3", "contas_que_repercutiram"] == 3
    assert df.loc["3", "fato_interacoes"] == 1010 and df.loc["5", "fato_id"] is pd_na()
    aba = relatorio.mesma_noticia(con)
    assert len(aba) == 1 and aba.loc[0, "perfis"] == 3 and aba.loc[0, "links"].count("\n") == 4


def pd_na():
    import pandas as pd

    return pd.NA


def test_a_ia_separa_caso_parecido_de_outro_lugar(con, municipios):
    mencao(con, "1", "akontecenews", 0, "Aluno morreu após sofrer choque elétrico na quadra do IFTO em Dianópolis.",
           _ibge(municipios, "Dianópolis"))
    mencao(con, "2", "gurupinews", 60 * 24, "Aluno morreu após sofrer choque elétrico na quadra de escola em Gurupi.",
           _ibge(municipios, "Gurupi"))
    cliente, chamadas = ia(lambda texto: None if "Gurupi" in texto.split("Candidatas")[0] else "A")
    resumo = fatos.consolidar(con, cliente, aviso=lambda _: None)
    assert resumo["ia_separou"] == 1 and len(chamadas) == 1
    assert fatos.fato_do_post(con, "instagram", "1") != fatos.fato_do_post(con, "instagram", "2")


def test_link_em_comum_junta_sem_ia_e_sem_ia_o_resto_espera(con):
    mencao(con, "1", "g1tocantins", 0, "Estudante morre em escola.", legenda=f"Leia: {G1}?utm_source=ig")
    mencao(con, "2", "outroportal", 30, "Tragédia no campus.", tipo="Story", legenda=f"Tragédia\nLink: {G1}")
    mencao(con, "3", "terceiro", 60, "Estudante morre em escola, diz polícia.")
    resumo = fatos.consolidar(con, None, aviso=lambda _: None)  # IA fora do ar
    assert resumo["novos"] == 1 and resumo["link"] == 1 and resumo["esperando_ia"] == 1
    assert fatos.fato_do_post(con, "instagram", "1") == fatos.fato_do_post(con, "instagram", "2")
    assert [m["post_id"] for m in fatos.pendentes(con)] == ["3"]


def test_interpretar_resposta_invalida_vira_agrupamento_novo():
    lote = [{"candidatos": [{"fato_id": 7}]}, {"candidatos": [{"fato_id": 8}, {"fato_id": 9}]}, {"candidatos": []}]
    assert fatos.interpretar({"1": "a", "2": "Z", "3": "A"}, lote) == [7, None, None]
    assert fatos.interpretar("lixo", lote) == [None, None, None]


def test_limpar_refazer_e_correcao_manual(con, municipios):
    diano = _ibge(municipios, "Dianópolis")
    for i, minuto in enumerate((0, 10, 20), 1):
        mencao(con, str(i), f"conta{i}", minuto, "Aluno morreu após choque elétrico no IFTO em Dianópolis.", diano)
    cliente, _ = ia()
    fatos.consolidar(con, cliente, aviso=lambda _: None)
    assert fatos.resumo(con, fatos.fato_do_post(con, "instagram", "1"))["posts"] == 3
    novo = fatos.mover(con, "instagram", "3", None)  # correção à mão: o 3 vira outro fato
    assert fatos.fato_do_post(con, "instagram", "3") == novo
    assert fatos.resumo(con, fatos.fato_do_post(con, "instagram", "1"))["posts"] == 2
    separa, _ = ia(lambda texto: None)
    fatos.refazer(con, separa, aviso=lambda _: None)  # desta vez a IA separa tudo
    assert fatos.fato_do_post(con, "instagram", "3") == novo  # a correção à mão fica
    assert con.execute("SELECT como FROM fato_mencao WHERE post_id = '3'").fetchone()[0] == "manual"
    assert con.execute("SELECT COUNT(*) FROM fatos").fetchone()[0] == 3
    # Reanalisada, a menção 2 deixou de ser sobre energia: sai, e o agrupamento vazio some.
    con.execute("UPDATE mencoes SET sobre_energia = 0 WHERE post_id = '2'")
    assert fatos.limpar(con) == 1 and fatos.fato_do_post(con, "instagram", "2") is None
    assert con.execute("SELECT COUNT(*) FROM fatos").fetchone()[0] == 2


def test_uma_consolidacao_por_vez(con):
    agora = datetime.now(timezone.utc)
    assert fatos.reservar_vez(con, "conta2", agora) is True
    assert fatos.reservar_vez(con, "conta3", agora) is False
    fatos.liberar_vez(con)
    assert fatos.reservar_vez(con, "conta3", agora) is True


def test_revisao_junta_agrupamentos_separados(con, municipios):
    diano, gurupi = _ibge(municipios, "Dianópolis"), _ibge(municipios, "Gurupi")
    # O post e o story do mesmo portal, 11 s depois, que a consolidação deixou separados.
    mencao(con, "1", "acordacolinas", 0, "Portal alerta que boato sobre apagão mundial de três dias voltou a circular.")
    mencao(con, "2", "acordacolinas", 0.2, "Portal alerta que voltou a circular boato sobre apagão mundial de três dias.",
           tipo="Story")
    # A mesma matéria explicativa em dois sites: a IA junta.
    mencao(con, "3", "soudepalmas", 60, "Matéria explica quando procurar a prefeitura ou a Energisa por lâmpada queimada.")
    mencao(con, "4", "enoticia", 120, "Matéria explica de quem é a responsabilidade pela iluminação pública.")
    # Casos parecidos em lugares diferentes: a IA separa, e o par não é perguntado de novo.
    mencao(con, "5", "akontecenews", 200, "Aluno morreu após choque elétrico na quadra do IFTO.", diano)
    mencao(con, "6", "gurupinews", 300, "Aluno levou choque elétrico na quadra de escola e foi socorrido.", gurupi)
    for m in fatos.pendentes(con):
        fatos.criar(con, m, "novo")
    con.commit()

    def responder(params):
        texto = params["messages"][1]["content"]
        pares = re.split(r"\n\n(?=\[\d+\])", texto)
        return resposta_deepseek({str(i): "lâmpada" in p for i, p in enumerate(pares, 1)})

    cliente, chamadas = deepseek_falso(responder)
    resumo = fatos.revisar(con, cliente, aviso=lambda _: None)
    assert resumo["juntados"] == 2 and resumo["separados"] >= 1 and len(chamadas) >= 1
    assert fatos.fato_do_post(con, "instagram", "1") == fatos.fato_do_post(con, "instagram", "2")  # sem IA
    assert fatos.fato_do_post(con, "instagram", "3") == fatos.fato_do_post(con, "instagram", "4")  # pela IA
    assert fatos.fato_do_post(con, "instagram", "5") != fatos.fato_do_post(con, "instagram", "6")
    como = dict(con.execute("SELECT como, COUNT(*) FROM fato_pares WHERE mesmo = 1 GROUP BY como").fetchall())
    assert como == {"texto": 1, "ia": 1}
    titulo = fatos.resumo(con, fatos.fato_do_post(con, "instagram", "1"))["titulo"]
    assert titulo.startswith("Portal alerta que boato")  # o título do que apareceu primeiro
    antes = len(chamadas)
    assert fatos.revisar(con, cliente, aviso=lambda _: None)["pares"] == 0 and len(chamadas) == antes


def test_consolidacao_completa_agrupa_as_pendentes_e_revisa_tambem_os_pares_antigos(con):
    # O post e o story do mesmo portal ficaram em agrupamentos separados há dias: a revisão da vigilância, que olha
    # só os últimos 3 dias, não os pega mais.
    mencao(con, "1", "acordacolinas", 0, "Portal alerta que boato sobre apagão mundial de três dias voltou a circular.")
    mencao(con, "2", "acordacolinas", 0.2, "Portal alerta que voltou a circular boato sobre apagão mundial de três dias.",
           tipo="Story")
    for m in fatos.pendentes(con):
        fatos.criar(con, m, "novo")
    con.commit()
    assert fatos.revisar(con, dias=3, dry_run=True)["pares"] == 0
    mencao(con, "3", "soudepalmas", 60, "Energisa anuncia mutirão de negociação de contas em Palmas.")  # pendente
    cliente, chamadas = ia()
    resumo = fatos.consolidar_tudo(con, cliente)
    assert resumo["ligadas"] == 1 and resumo["juntados"] == 1
    assert fatos.fato_do_post(con, "instagram", "1") == fatos.fato_do_post(con, "instagram", "2")
    assert fatos.fato_do_post(con, "instagram", "3") not in (None, fatos.fato_do_post(con, "instagram", "1"))
    assert chamadas == []  # nada aqui precisou da IA
    assert fatos.reservar_vez(con, "conta2", datetime.now(timezone.utc)) is True  # a vez foi devolvida


def test_consolidacao_completa_espera_a_vez_da_vigilancia(con):
    agora = [datetime.now(timezone.utc)]
    dormidos = []

    def dormir(segundos):
        dormidos.append(segundos)
        agora[0] += timedelta(seconds=segundos)

    assert fatos.reservar_vez(con, "conta2", agora[0]) is True  # a vigilância está consolidando
    assert fatos.consolidar_tudo(con, None, esperar=60, dormir=dormir, relogio=lambda: agora[0]) is None
    assert dormidos == [15, 15, 15, 15]
    assert fatos.reservar_vez(con, "conta3", agora[0]) is False  # a vez continua com a vigilância
    fatos.liberar_vez(con)
    assert fatos.consolidar_tudo(con, None, esperar=60, dormir=dormir, relogio=lambda: agora[0]) is not None
