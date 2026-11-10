from radar import classificacao as cl, config, enriquecimento, influentes as inf, monitoramento as mon, perfis

from conftest import classificacao, perfil, post


class NavegadorFalso:
    """Busca e perfis (com as contas parecidas) com respostas fixas; cada pedido conta como uma leitura."""

    lote_maximo = 10

    def __init__(self, buscas=None, perfis_=None):
        self.buscas, self.perfis = buscas or {}, perfis_ or {}
        self.leituras = 0
        self.lidos = []

    def pesquisar(self, termo):
        self.leituras += 1
        return self.buscas.get(termo, [])

    def rodar(self, ator, entrada, rotulo, max_usd=None):
        assert ator == config.ATOR_PERFIL
        self.leituras += len(entrada["usernames"])
        self.lidos += entrada["usernames"]
        return "nav", [self.perfis.get(u, {"username": u, "error": "not_found"}) for u in entrada["usernames"]]


def usuario(username, nome, privada=False, id_="0"):
    return {"username": username, "nome": nome, "id": id_, "privada": privada, "verificada": False}


def parecida(username, nome):
    return {"username": username, "full_name": nome, "is_private": False}


def test_evidencia_de_tocantins(con, municipios):
    for item, origem in [(perfil("maria", biography="Criadora em Palmas - TO"), "x"),
                         (perfil("paulista", biography="São Paulo capital"), "x"),
                         (perfil("viajante", biography="Viagens", latestPosts=[
                             post(1, codigo="L1") | {"locationName": "Gurupi, Tocantins"},
                             post(2, codigo="L2") | {"locationName": "Araguaína"}]), "x")]:
        perfis.ingerir(con, item, origem, municipios)
    ligadas = inf.ligacoes(con, inf.contas_do_to(con))
    conta_ = lambda u: dict(con.execute("SELECT * FROM contas WHERE username = ?", (u,)).fetchone())  # noqa: E731
    assert inf.evidencia_to(con, conta_("maria"), municipios, ligadas)
    assert not inf.evidencia_to(con, conta_("paulista"), municipios, ligadas)
    assert inf.evidencia_to(con, conta_("viajante"), municipios, ligadas)  # posts marcados em cidades do TO
    assert inf.evidencia_to(con, conta_("paulista"), municipios, {"paulista": 2})  # duas contas do TO apontam


def test_rodada_busca_parecidas_perfis_e_triagem(con, municipios, monkeypatch):
    monkeypatch.setattr(inf, "TERMOS", ["tocantins"])
    # Semente: um político do TO já classificado, com 50 mil seguidores e sem sugestões guardadas.
    perfis.ingerir(con, perfil("deputado", followersCount=50_000, biography="Deputado do Tocantins"),
                   "busca", municipios)
    cl.salvar(con, "deputado", classificacao(tipo="politico", municipios=["Palmas"]), municipios, "m")
    navegador = NavegadorFalso(
        buscas={"tocantins": [usuario("cantora.to", "Cantora do Tocantins"), usuario("oculta", "x", privada=True)],
                "Palmas": [usuario("palmeirense", "Torcedor")]},
        perfis_={
            # A visita à página do deputado traz as contas parecidas que o Instagram sugere.
            "deputado": perfil("deputado", followersCount=50_000, biography="Deputado do Tocantins",
                               relatedProfiles=[parecida("humorista_palmas", "Humor de Palmas"),
                                                parecida("loja_x", "Loja X Delivery"),
                                                parecida("pequena.palmas", "Pequena de Palmas")]),
            "cantora.to": perfil("cantora.to", followersCount=60_000, biography="Cantora tocantinense"),
            "humorista_palmas": perfil("humorista_palmas", followersCount=12_000, biography="Humor de Palmas"),
            "palmeirense": perfil("palmeirense", followersCount=40_000, biography="Torcedor do Corinthians, SP"),
            "pequena.palmas": perfil("pequena.palmas", followersCount=3_000, biography="Palmas"),
        })
    resumo = inf.rodada(con, navegador, orcamento=40, aviso=lambda _: None)

    assert resumo["pesquisas"] >= 2 and resumo["semelhantes"] == 1
    assert "loja_x" not in navegador.lidos and "oculta" not in navegador.lidos  # loja e privada nem são lidas
    descartadas = dict(con.execute("SELECT username, descartada FROM contas WHERE origem = 'influentes'").fetchall())
    assert descartadas["cantora.to"] is None and descartadas["humorista_palmas"] is None
    assert descartadas["palmeirense"] == "fora_do_to"
    assert descartadas["pequena.palmas"] == "abaixo_do_corte"
    assert resumo["aceitas"] == 3 and resumo["corte"] == inf.CORTE_INICIAL  # deputado, cantora e humorista
    assert con.execute("SELECT n FROM semelhantes WHERE username = 'deputado'").fetchone()[0] == 3
    assert inf.situacao(con)["pesquisas_feitas"] == resumo["pesquisas"]

    # Uma rodada nova não visita de novo quem já teve as sugestões lidas (a semente e os perfis lidos).
    segundo = NavegadorFalso()
    inf.rodada(con, segundo, orcamento=10, aviso=lambda _: None)
    assert not {"deputado", "cantora.to", "humorista_palmas"} & set(segundo.lidos)


def test_corte_e_a_milesima_conta():
    contas = [{"seguidores": s} for s in (90_000, 50_000, 30_000)]
    assert inf.corte(contas, piso=10_000, tamanho=2) == 50_000
    assert inf.corte(contas, piso=10_000, tamanho=5) == 10_000  # ainda não há contas suficientes


def test_irrelevante_antiga_do_to_volta_para_a_ia(con, municipios):
    perfis.ingerir(con, perfil("artista", followersCount=30_000, biography="Artista plástica de Gurupi"),
                   "relacionada", municipios)
    cl.salvar(con, "artista", classificacao(tipo="irrelevante", publica_noticias_locais=False), municipios, "m")
    con.execute("UPDATE classificacoes SET classificada_em = '2026-10-01T00:00:00+00:00'")
    motivos = inf.triagem(con, municipios, inf.ligacoes(con, inf.contas_do_to(con)), 10_000)
    assert motivos["volta_para_a_ia"] == 1
    assert con.execute("SELECT COUNT(*) FROM classificacoes").fetchone()[0] == 0


def test_filtro_de_comercio_nao_derruba_influenciadora_de_moda():
    conta = {"username": "anasouza", "origem": enriquecimento.ORIGEM_INFLUENTES, "completo": 1,
             "seguidores": 80_000, "n_posts": 900, "nome": "Ana Souza", "bio": "Moda e beleza | cupom ANA10"}
    assert enriquecimento.motivo_descarte(conta) is None
    assert enriquecimento.motivo_descarte(conta | {"origem": "relacionada"}) == "comercial"


def test_lista_entra_na_base_monitorada(con, municipios):
    contas = [("cantora", "influenciador_local", 90_000), ("deputado", "politico", 70_000),
              ("reporter", "jornalista_radialista", 20_000), ("portal", "portal_noticias", 99_000),
              ("prefeitura", "orgao_publico", 98_000), ("loja", "irrelevante", 97_000),
              ("rejeitada", "influenciador_local", 95_000), ("goiana", "influenciador_local", 80_000)]
    for username, tipo, seguidores in contas:
        perfis.ingerir(con, perfil(username, followersCount=seguidores), "influentes", municipios)
        cidades = [] if username == "goiana" else ["Palmas"]  # sem município do TO pela IA: fora
        cl.salvar(con, username, classificacao(tipo=tipo, municipios=cidades, publica_noticias_locais=False),
                  municipios, "m")
    con.execute("INSERT INTO revisao (rede, username, status) VALUES ('instagram', 'rejeitada', 'rejeitada')")

    # Só a contagem de seguidores ordena; páginas entram, órgão público e loja não.
    lista = inf.montar_lista(con, tamanho=2)
    assert [l["username"] for l in lista] == ["portal", "cantora"]
    assert {c["username"] for c in mon.monitoradas(con)} == {"portal", "cantora"}
    assert [l["username"] for l in inf.montar_lista(con)] == ["portal", "cantora", "deputado", "reporter"]
    assert con.execute("SELECT COUNT(*) FROM listas").fetchone()[0] == 4  # a lista é refeita, não acumulada


def test_ranking_sem_empresas(con, municipios):
    contas = [  # username, seguidores, tipo da IA (None = sem classificação), município da IA, bio
        ("prefeitura", 99_000, "orgao_publico", ["Palmas"], "Prefeitura"),
        ("supermercado", 98_000, "irrelevante", ["Palmas"], "Ofertas"),
        ("portal_ba", 97_000, "portal_noticias", [], "Notícias de Cruz das Almas, Bahia"),
        ("cantora", 96_000, None, None, "Cantora de Palmas, Tocantins"),
        ("boutique_palmas", 95_000, None, None, "Moda feminina em Palmas"),
        ("portal_to", 50_000, "portal_noticias", ["Gurupi"], "Notícias"),
    ]
    for username, seguidores, tipo, cidades, bio in contas:
        perfis.ingerir(con, perfil(username, followersCount=seguidores, biography=bio), "influentes", municipios)
        if tipo:
            cl.salvar(con, username, classificacao(tipo=tipo, municipios=cidades), municipios, "m")

    ranking = inf.ranking(con, tamanho=10)
    # Fora: o supermercado (empresa, pela IA), o portal que a IA não ligou ao TO e a loja ainda sem classificação.
    assert [c["username"] for c in ranking] == ["prefeitura", "cantora", "portal_to"]
    assert ranking[1]["tipo"] is None  # entra como "a confirmar" até o classificar
    assert [c["username"] for c in inf.ranking(con, tamanho=10, com_orgaos=False)] == ["cantora", "portal_to"]
    assert len(inf.ranking(con, tamanho=1)) == 1


def test_conta_estadual_sem_municipio_entra(con, municipios):
    perfis.ingerir(con, perfil("governador", followersCount=180_000, biography="Governador"), "busca", municipios)
    cl.salvar(con, "governador", classificacao(tipo="politico", municipios=[], abrangencia="estadual"), municipios, "m")
    assert [c["username"] for c in inf.ranking(con, tamanho=5)] == ["governador"]
    assert [c["username"] for c in inf.montar_lista(con, tamanho=5)] == ["governador"]


def test_ranking_de_influenciadores(con, municipios):
    contas = [  # username, seguidores, categoria, bio, tipo da IA, classificada depois do prompt novo
        ("gabi", 90_000, "Digital creator", "Criadora de conteúdo | Palmas - TO", None, False),
        ("pousada_lago", 80_000, "Digital creator", "Pousada às margens do lago em Palmas", None, False),
        ("deputado", 70_000, "Politician", "Deputado do Tocantins", "politico", True),
        ("vaqueiro", 60_000, "", "Vaqueiro de Gurupi | publi: contato comercial no direct", "irrelevante", False),
        ("loja_ana", 50_000, "Digital creator", "Moda em Palmas", "irrelevante", True),
        ("mika", 40_000, "", "Palmas", "influenciador_local", True),
        ("palmascitytv", 35_000, "", "Notícias de Palmas, TO | publi e parcerias no direct", None, False),
        ("acorda_paraiso", 30_000, "Site de notícias e mídia", "Paraíso do Tocantins", None, False),
        ("ana.souza", 20_000, "", "Palmas - TO | publi: contato no direct", None, False),
    ]
    nomes = {"ana.souza": "Ana Souza", "vaqueiro": "Edvaldo Rodrigues"}
    for username, seguidores, categoria, bio, tipo, novo in contas:
        perfis.ingerir(con, perfil(username, followersCount=seguidores, biography=bio, businessCategoryName=categoria,
                                   fullName=nomes.get(username, username.title())), "influentes", municipios)
        if tipo:
            cl.salvar(con, username, classificacao(tipo=tipo, municipios=["Palmas"]), municipios, "m")
            if not novo:
                con.execute("UPDATE classificacoes SET classificada_em = '2026-10-01' WHERE username = ?", (username,))

    ranking = inf.ranking_influenciadores(con, 10)
    # Fora: a pousada (negócio no nome), o deputado (político), a loja que a IA nova disse ser irrelevante, o portal
    # (nome de página, mesmo com "publi" na bio) e o site de notícias (categoria de veículo).
    assert [c["username"] for c in ranking] == ["gabi", "vaqueiro", "mika", "ana.souza"]
    assert [c["confirmada"] for c in ranking] == [False, False, True, False]
    assert [c["username"] for c in inf.a_confirmar(con)] == ["gabi", "vaqueiro", "ana.souza"]  # o que vai para a IA
