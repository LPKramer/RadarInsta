from datetime import datetime, timedelta, timezone

from radar import classificacao as cl, db, fofoca, monitoramento as mon, navegador as nav, perfis

from conftest import classificacao, perfil
from test_influentes import NavegadorFalso

AGORA = datetime.now(timezone.utc)


def _municipio(municipios, nome):
    return next(m for m in municipios if m["nome"] == nome)


def media(dono, nome, legenda, curtidas=100, dias=3, privada=False, codigo=None):
    """Post como vem na grade da busca por palavra-chave do site, com o autor resumido."""
    codigo = codigo or f"{dono}{curtidas}{dias}"
    return {"pk": f"pk{codigo}", "code": codigo, "media_type": 1, "caption": {"text": legenda},
            "taken_at": int((AGORA - timedelta(days=dias)).timestamp()), "like_count": curtidas, "comment_count": 3,
            "user": {"username": dono, "full_name": nome, "is_private": privada}}


class NavegadorPosts(NavegadorFalso):
    """A busca de posts com respostas fixas por termo; cada busca conta como uma leitura."""

    def __init__(self, posts=None, perfis_=None):
        super().__init__(perfis_=perfis_)
        self.posts = posts or {}
        self.termos = []

    def buscar_posts(self, termo):
        self.leituras += 1
        self.termos.append(termo)
        return [nav.post_do_site(m) for m in self.posts.get(termo, [])]


def test_termos_por_porte_nome_curto_e_homonimos(municipios):
    hom = fofoca.homonimos(municipios)
    assert fofoca.termos(_municipio(municipios, "Colinas do Tocantins"), hom) == ["colinas to", "fofoca colinas to"]
    assert fofoca.termos(_municipio(municipios, "Aliança do Tocantins"), hom) == ["alianca to"]
    # Homônima de cidade de fora (Lajeado-RS) ou palavra comum (peixe): também leva o "to". Palmas, das maiores, não.
    assert fofoca.termos(_municipio(municipios, "Lajeado"), hom) == ["lajeado to"]
    assert fofoca.termos(_municipio(municipios, "Peixe"), hom) == ["peixe to"]
    assert fofoca.termos(_municipio(municipios, "Palmas"), hom) == ["palmas", "fofoca palmas", "noticias palmas"]
    todos = [t for m in municipios for t in fofoca.termos(m, hom)]
    assert len(todos) == len(set(todos))  # nenhum termo repetido entre municípios


def test_homonimos_de_fora_do_to(municipios):
    def cidades(username, nome, alvo=None):
        return fofoca.cidades_citadas(username, nome, municipios, alvo and _municipio(municipios, alvo))

    taguatinga = _municipio(municipios, "Taguatinga")["ibge_id"]
    paraiso = _municipio(municipios, "Paraíso do Tocantins")["ibge_id"]
    colinas = _municipio(municipios, "Colinas do Tocantins")["ibge_id"]
    # Lugar de fora cujo nome contém o de uma cidade do TO.
    assert not cidades("valparaisonews", "Valparaíso News", "Paraíso do Tocantins")
    assert not cidades("jiparana.news", "Ji-Paraná News", "Paranã")
    assert not cidades("buritis.news", "Buritis News", "Buriti do Tocantins")
    assert cidades("paraisonews", "Paraíso News", "Paraíso do Tocantins") == {paraiso}  # das maiores: vale sozinha
    # Homônima (Taguatinga do DF, Colinas do MA) ou palavra comum: só com sinal de TO.
    assert not cidades("taguatinga_news", "Taguatinga News", "Taguatinga")
    assert cidades("taguatinga.to.news", "Taguatinga News", "Taguatinga") == {taguatinga}
    assert cidades("taguatinganews", "Taguatinga - TO", "Taguatinga") == {taguatinga}
    assert cidades("colinas_tonews", "Colinas News", "Colinas do Tocantins") == {colinas}
    assert cidades("acordacolinas063", "Acorda Colinas", "Colinas do Tocantins") == {colinas}
    assert not cidades("acordacolinas", "Acorda Colinas", "Colinas do Tocantins")
    assert not cidades("", "Peixe frito no almoço de domingo")
    assert cidades("", "Festa em Peixe-TO neste sábado") == {_municipio(municipios, "Peixe")["ibge_id"]}


def test_so_le_autor_ativo_e_curtido_que_fala_de_cidade_do_to(municipios):
    gurupi = _municipio(municipios, "Gurupi")
    medias = [
        media("enquantoissoemgurupi_", "Enquanto isso em Gurupi", "Acidente na BR-153"),  # cidade no @: vale
        media("fraganewsoficial", "Fraga News", "Feira coberta de Gurupi reabre", curtidas=33),  # cidade na legenda
        media("portal_velho", "Portal Velho", "Gurupi em festa", dias=300),            # sem post recente
        media("portal_fraco", "Portal Fraco", "Gurupi em festa", curtidas=10),         # quase sem curtidas
        media("jair.inocencio", "Jair Inocêncio", "Obrigado, Gurupi!", curtidas=500),  # pessoa: só com 1.000
        media("deputada.x", "Deputada X", "Obrigada, Gurupi!", curtidas=2500),
        media("segredo_gurupi", "Segredo", "Gurupi", privada=True),
        media("loja.gurupi", "Loja Gurupi Calçados", "Promoção em Gurupi", curtidas=900),
        media("energisatocantins", "Energisa Tocantins", "Obra em Gurupi"),
        media("babadosipatinga", "Tia dos Babados", "Fofoca quente de hoje", curtidas=4000),  # nada do TO
        media("semnome_gurupi", "", "Gurupi"),
    ]
    itens = [nav.post_do_site(m) for m in medias]
    escolhidas = fofoca.candidatas_dos_posts(itens, gurupi, municipios, AGORA)
    assert sorted(escolhidas) == ["deputada.x", "enquantoissoemgurupi_", "fraganewsoficial"]
    assert escolhidas["fraganewsoficial"]["cidades"] == {gurupi["ibge_id"]}


def test_lidas_de_fora_do_to_sao_descartadas_antes_da_ia(con, municipios):
    tag = _municipio(municipios, "Taguatinga")["ibge_id"]
    gurupi = _municipio(municipios, "Gurupi")["ibge_id"]
    for item in (perfil("taguatinga_news", fullName="Taguatinga News", biography="Taguatinga e região, DF"),
                 perfil("taguatinga24h", fullName="Taguatinga 24h", biography="Taguatinga - Tocantins"),
                 perfil("rondapolicial", fullName="Ronda Policial", biography="O maior do Tocantins"),
                 perfil("fraganewsoficial", fullName="Fraga News", biography="Notícias"),
                 perfil("jiparana.news", fullName="Ji-Paraná News", biography="Ji-Paraná e região")):
        perfis.ingerir(con, item, fofoca.ORIGEM, municipios, 1)
        con.execute("INSERT INTO tarefas (rodada, username, estado) VALUES (?, ?, 'feita')",
                    (fofoca.RODADA, item["username"]))
    db.vincular_municipio(con, "taguatinga_news", tag, "busca")  # ligada pela regra antiga
    db.vincular_municipio(con, "fraganewsoficial", gurupi, "busca")  # a legenda citava Gurupi
    assert fofoca.descartar_de_fora(con, municipios) == 2
    descartadas = dict(con.execute("SELECT username, descartada FROM contas WHERE descartada IS NOT NULL"))
    assert descartadas == {"taguatinga_news": "fora_do_to", "jiparana.news": "fora_do_to"}
    assert sorted(c["username"] for c in fofoca.a_classificar(con)) == [
        "fraganewsoficial", "rondapolicial", "taguatinga24h"]


def test_rodada_busca_le_e_classifica(con, municipios):
    # Já conhecidas: uma só de nome (contas parecidas) e uma lida e nunca classificada.
    con.execute("INSERT INTO relacionadas (rede, origem, relacionada, nome) "
                "VALUES ('instagram', 'x', 'gurupi_acontece', 'Gurupi Acontece')")
    con.execute("INSERT INTO relacionadas (rede, origem, relacionada, nome) "
                "VALUES ('instagram', 'x', 'fuxicogurupi', 'Fuxico de Gurupi')")  # anônima: fica de fora
    perfis.ingerir(con, perfil("araguaina_news", followersCount=4000), "busca", municipios)
    perfis.ingerir(con, perfil("palmas_antiga", followersCount=9000), "busca", municipios)
    cl.salvar(con, "palmas_antiga", classificacao(tipo="portal_noticias", municipios=["Palmas"]), municipios, "m")
    con.commit()
    navegador = NavegadorPosts(
        posts={"palmas": [media("tuviu_palmas", "Tu Viu Palmas", "Show hoje em Palmas", curtidas=800),
                          media("palmas_antiga", "Palmas Antiga", "Palmas", curtidas=900),  # já resolvida
                          media("fofocapalmas", "Fofoca", "Palmas", curtidas=2),
                          media("morador", "Morador", "Falta de energia em Palmas desde ontem, Energisa!",
                                curtidas=12, codigo="E1")],
               "peixe to": [media("peixe_news", "Peixe News", "Peixe-TO em festa"),
                            media("tuviu_palmas", "Tu Viu", "Palmas")]},
        perfis_={"tuviu_palmas": perfil("tuviu_palmas", followersCount=20_000, biography="Notícias de Palmas"),
                 "peixe_news": perfil("peixe_news", followersCount=150),  # abaixo do mínimo de seguidores
                 "gurupi_acontece": perfil("gurupi_acontece", followersCount=3000, biography="Gurupi TO")})
    classificadas = []

    def classificar(contas):
        for c in contas:
            classificadas.append(c["username"])
            cl.salvar(con, c["username"], classificacao(tipo="perfil_fofoca", municipios=["Palmas"]), municipios, "m")
        con.commit()

    resumo = fofoca.rodada(con, navegador, orcamento=10_000, aviso=lambda _: None, classificar=classificar)

    assert resumo["buscas_faltando"] == 0 and resumo["pesquisas"] == resumo["buscas_feitas"]
    assert sorted(navegador.lidos) == ["gurupi_acontece", "peixe_news", "tuviu_palmas"]  # cada uma lida uma vez
    assert sorted(classificadas) == ["araguaina_news", "gurupi_acontece", "tuviu_palmas"]  # a lida antes vai direto
    assert con.execute("SELECT descartada FROM contas WHERE username = 'peixe_news'").fetchone()[0] \
        == "poucos_seguidores"
    vigiadas = {c["username"] for c in mon.monitoradas(con)}
    assert {"tuviu_palmas", "gurupi_acontece"} <= vigiadas
    assert resumo["paginas_de_fofoca"] == 3 and resumo["municipios_com_fofoca"] == 1
    # O post sobre energia que apareceu na busca já entra nas menções.
    assert resumo["posts_sobre_energia"] == 1
    assert [l[0] for l in con.execute("SELECT termo FROM posts_busca")] == ["palmas"]

    # Retomada: nada é pesquisado nem lido de novo.
    leituras = navegador.leituras
    fofoca.rodada(con, navegador, orcamento=10_000, aviso=lambda _: None, classificar=classificar)
    assert navegador.leituras == leituras


def test_orcamento_para_no_meio_e_retoma(con):
    navegador = NavegadorPosts()
    fofoca.rodada(con, navegador, orcamento=5, aviso=lambda _: None)
    assert navegador.leituras == 5
    assert fofoca.situacao(con)["buscas_feitas"] == 5
    fofoca.rodada(con, navegador, orcamento=3, aviso=lambda _: None)
    assert fofoca.situacao(con)["buscas_feitas"] == 8
    assert db.termos_executados(con, fofoca.FONTE) >= {"palmas", "fofoca palmas"}
