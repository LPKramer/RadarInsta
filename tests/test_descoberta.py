import sqlite3

from radar import classificacao as cl, config, db, descoberta, perfis, termos as tm

from conftest import ApifyFalso, classificacao, perfil


def test_planejar_pula_termos_ja_executados(con):
    termos = descoberta.planejar_busca(con, "Palmas")
    assert len(termos) == 12
    db.registrar_busca(con, "busca", termos[0].termo, termos[0].ibge_id, "run", 5)
    assert len(descoberta.planejar_busca(con, "Palmas")) == 11


def test_executar_busca_vincula_municipio_do_termo(con):
    termos = descoberta.planejar_busca(con, "Gurupi")
    itens = [perfil("portalsul", searchTerm="gurupi noticias"), perfil("semtermo")]
    apify = ApifyFalso({config.ATOR_BUSCA: itens})
    assert descoberta.executar_busca(con, apify, termos, aviso=lambda _: None) == 2

    entrada = apify.chamadas[0][1]
    assert entrada["searchType"] == "user" and "gurupi noticias" in entrada["search"]
    vinculos = con.execute("SELECT username, fonte FROM conta_municipio").fetchall()
    assert [tuple(v) for v in vinculos] == [("portalsul", "busca")]
    feitos = {r["termo"]: r["n_resultados"] for r in con.execute("SELECT * FROM buscas")}
    assert feitos["gurupi noticias"] == 1 and len(feitos) == len(termos)


def test_hashtags_exigem_ocorrencias_minimas(con):
    itens = [{"ownerUsername": "Frequente", "hashtags": ["palmasto"]}] * 3 + [{"ownerUsername": "raro"}]
    novas, por_tag = descoberta.ingerir_hashtags(con, itens, min_ocorrencias=2)
    assert novas == 1 and por_tag["palmasto"] == 3
    assert [r["username"] for r in con.execute("SELECT username FROM contas")] == ["frequente"]


def test_google_extrai_perfis_das_urls(con, municipios):
    consulta = descoberta.Termo('site:instagram.com "Gurupi"', municipios[2]["ibge_id"])
    itens = [{"searchQuery": {"term": consulta.termo}, "organicResults": [
        {"url": "https://www.instagram.com/gurupiagora/"},
        {"url": "https://www.instagram.com/p/abc/", "title": "Acidente na BR-153 ..."},
        # Post que chega como redirecionamento do Google: o autor vem do título.
        {"url": "https://www.google.com/goto?url=CAESZw", "title": "Gurupi Alerta (@GurupiAlerta) • Instagram"},
    ]}]
    novas, contagem = descoberta.ingerir_google(con, itens, {consulta.termo: consulta})
    assert novas == 2 and contagem[consulta.termo] == 2
    assert {r["username"] for r in con.execute("SELECT username FROM contas")} == {"gurupiagora", "gurupialerta"}


def test_consulta_google_usa_nome_oficial_e_avanca_de_modelo(con):
    sao_miguel = {"nome": "São Miguel do Tocantins", "ibge_id": 1}
    (primeira,) = descoberta.planejar_google(con, [sao_miguel])
    assert primeira.termo.startswith('site:instagram.com "São Miguel do Tocantins" (noticias OR fofoca')
    db.registrar_busca(con, "google", primeira.termo, 1, "run", 0)
    (segunda,) = descoberta.planejar_google(con, [sao_miguel])
    assert segunda.termo == 'site:instagram.com "São Miguel do Tocantins" tocantins'
    db.registrar_busca(con, "google", segunda.termo, 1, "run", 0)
    assert descoberta.planejar_google(con, [sao_miguel]) == []


def test_pouca_cobertura(con, municipios):
    perfis.ingerir(con, perfil("gurupinoticias"), "busca", municipios)
    cl.salvar(con, "gurupinoticias", classificacao(), municipios, "m")  # a IA liga a conta a Gurupi
    con.execute("INSERT INTO score (rede, username, valor, prioridade, calculado_em) "
                "VALUES ('instagram', 'gurupinoticias', 60, 'A', 'x')")
    gurupi = next(m for m in municipios if m["nome"] == "Gurupi")
    assert gurupi not in descoberta.pouca_cobertura(con, municipios, 0)
    assert gurupi in descoberta.pouca_cobertura(con, municipios, 1)
    assert len(descoberta.pouca_cobertura(con, municipios, 0)) == len(municipios) - 1
    con.execute("INSERT INTO revisao (rede, username, status) VALUES ('instagram', 'gurupinoticias', 'rejeitada')")
    assert gurupi in descoberta.pouca_cobertura(con, municipios, 0)


def test_expandir_so_a_partir_de_contas_relevantes(con, municipios):
    relevante = perfil("portal", relatedProfiles=[{"username": "vizinho1"}, {"username": "vizinho2"}])
    loja = perfil("loja", relatedProfiles=[{"username": "outraloja"}])
    for item in (relevante, loja):
        perfis.ingerir(con, item, "busca", municipios)
    cl.salvar(con, "portal", classificacao(), municipios, "m")
    cl.salvar(con, "loja", classificacao(tipo="irrelevante", publica_noticias_locais=False), municipios, "m")

    assert descoberta.expandir(con) == 2
    novas = {r["username"]: r["profundidade"] for r in con.execute("SELECT * FROM contas WHERE origem = 'relacionada'")}
    assert novas == {"vizinho1": 1, "vizinho2": 1}
    assert descoberta.expandir(con) == 0  # idempotente


def test_expandir_respeita_rejeicao_e_profundidade(con, municipios):
    perfis.ingerir(con, perfil("portal", relatedProfiles=[{"username": "v"}]), "busca", municipios)
    cl.salvar(con, "portal", classificacao(), municipios, "m")
    con.execute("INSERT INTO revisao (rede, username, status) VALUES ('instagram', 'portal', 'rejeitada')")
    assert descoberta.expandir(con) == 0
    con.execute("DELETE FROM revisao")
    assert descoberta.expandir(con, profundidade_max=0) == 0
    assert descoberta.expandir(con, confianca_min=0.95) == 0


def test_expandir_so_de_portais_e_paginas_por_padrao(con, municipios):
    perfis.ingerir(con, perfil("prefeitura", relatedProfiles=[{"username": "secretaria"}]), "busca", municipios)
    perfis.ingerir(con, perfil("goias", relatedProfiles=[{"username": "outra"}]), "busca", municipios)
    cl.salvar(con, "prefeitura", classificacao(tipo="orgao_publico"), municipios, "m")
    cl.salvar(con, "goias", classificacao(municipios=["Goiânia"]), municipios, "m")  # nenhum município do TO
    assert descoberta.expandir(con) == 0
    assert descoberta.expandir(con, tipos=None) == 1


def test_candidatas_por_sinais(con, municipios):
    rel = lambda *nomes: [{"username": u, "full_name": n} for u, n in nomes]
    perfis.ingerir(con, perfil("portal", relatedProfiles=rel(
        ("amigo", None), ("gurupialerta", "Gurupi Alerta"), ("lojadaana", "Loja da Ana"),
        ("portal2", None))), "busca", municipios)
    perfis.ingerir(con, perfil("portal2", relatedProfiles=rel(("amigo", "Amigo"))), "busca", municipios)
    perfis.ingerir(con, perfil("loja", relatedProfiles=rel(("jornalx", None), ("noticiasdegurupi", None))), "busca",
                   municipios)
    cl.salvar(con, "portal", classificacao(), municipios, "m")
    cl.salvar(con, "portal2", classificacao(), municipios, "m")
    cl.salvar(con, "loja", classificacao(tipo="irrelevante", publica_noticias_locais=False), municipios, "m")
    con.execute("UPDATE contas SET profundidade = 3 WHERE username = 'loja'")

    candidatas = {c["username"]: c for c in descoberta.candidatas_por_sinais(con, municipios)}
    # amigo: 2 contas relevantes; gurupialerta: palavra de notícia e 1 relevante; noticiasdegurupi: palavra de
    # notícia e município no @, mesmo vindo de conta irrelevante. Ficam de fora a loja, o jornal sem ligação
    # relevante nem município, e o portal2, que já está na base.
    assert set(candidatas) == {"amigo", "gurupialerta", "noticiasdegurupi"}
    assert candidatas["amigo"]["ligacoes"] == 2 and candidatas["amigo"]["nome"] == "Amigo"
    assert candidatas["noticiasdegurupi"]["profundidade"] == 4
    assert [c["username"] for c in descoberta.candidatas_por_sinais(con, municipios)][0] == "amigo"


def test_google_estado_com_varias_paginas(con, municipios):
    consultas = descoberta.planejar_google_estado(con, municipios)
    t1 = sum(1 for m in municipios if m["porte"] == "T1")
    assert len(consultas) == len(tm.CONSULTAS_GOOGLE_ESTADO) + t1 and t1 > 0
    apify = ApifyFalso({config.ATOR_GOOGLE: []})
    descoberta.executar_google(con, apify, consultas[:2], aviso=lambda _: None, paginas=3)
    assert apify.chamadas[0][1]["maxPagesPerQuery"] == 3
    assert len(descoberta.planejar_google_estado(con, municipios)) == len(consultas) - 2
    assert descoberta.custo_google(10, 3) == descoberta.custo_google(30)


def test_relacionadas_guardam_o_nome_e_o_banco_antigo_ganha_a_coluna(con, municipios, tmp_path):
    perfis.ingerir(con, perfil("a", relatedProfiles=[{"username": "b"}]), "busca", municipios)
    perfis.ingerir(con, perfil("a", relatedProfiles=[{"username": "b", "full_name": "Bê"}]), "busca", municipios)
    perfis.ingerir(con, perfil("a", relatedProfiles=[{"username": "b"}]), "busca", municipios)
    assert con.execute("SELECT nome FROM relacionadas").fetchone()[0] == "Bê"

    antigo = sqlite3.connect(tmp_path / "antigo.db")
    antigo.execute("CREATE TABLE relacionadas (rede TEXT, origem TEXT, relacionada TEXT, "
                   "PRIMARY KEY (rede, origem, relacionada))")
    antigo.close()
    novo = db.conectar(tmp_path / "antigo.db")
    assert "nome" in {l["name"] for l in novo.execute("PRAGMA table_info(relacionadas)")}
