from openpyxl import load_workbook

from radar import config, mencoes as mc, monitoramento as mon, perfis, relatorio

from conftest import ApifyFalso, analise, conta, deepseek_falso, perfil, post, resposta_deepseek


def test_posts_guardam_a_maior_contagem_de_engajamento(con, municipios):
    conta(con, municipios, "gurupinoticias", [post(1, curtidas=-1, comentarios=5, codigo="A1")])
    linha = con.execute("SELECT * FROM posts").fetchone()
    assert linha["curtidas"] is None and linha["comentarios"] == 5  # -1 = curtidas ocultas
    perfis.ingerir_posts(con, "gurupinoticias", [post(1, curtidas=80, comentarios=9, codigo="A1")])
    perfis.ingerir_posts(con, "gurupinoticias", [post(1, curtidas=50, comentarios=2, codigo="A1")])
    linha = con.execute("SELECT * FROM posts").fetchone()
    assert (linha["curtidas"], linha["comentarios"]) == (80, 9)
    perfis.ingerir_posts(con, "x", [post(1)])  # sem id nem shortCode: ignorado
    assert con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1


def test_monitoradas_sao_as_a_b_e_as_aprovadas_menos_as_rejeitadas(con, municipios):
    conta(con, municipios, "portal", [])
    conta(con, municipios, "pequena", [], prioridade="C")
    conta(con, municipios, "aprovada", [], prioridade="C")
    conta(con, municipios, "rejeitada", [])
    con.execute("INSERT INTO revisao (rede, username, status) VALUES ('instagram', 'aprovada', 'aprovada'), "
                "('instagram', 'rejeitada', 'rejeitada')")
    assert {c["username"] for c in mon.monitoradas(con)} == {"portal", "aprovada"}


def test_coletar_preserva_profundidade(con, municipios):
    conta(con, municipios, "portal", [])
    con.execute("UPDATE contas SET profundidade = 3")
    apify = ApifyFalso({"apify/instagram-profile-scraper": [perfil("portal", latestPosts=[post(0, codigo="N1")])]})
    assert mon.coletar(con, apify, mon.monitoradas(con), aviso=lambda _: None) == {"perfis": 1, "posts": 1}
    assert con.execute("SELECT profundidade FROM contas").fetchone()[0] == 3
    assert apify.chamadas[0][1] == {"usernames": ["portal"]}


def test_filtro_de_palavras_chave():
    for texto in ("Energisa anuncia obra", "FALTA DE ENERGIA no setor Sul", "Apagão atinge o centro",
                  "Conta de luz mais cara em novembro", "Poste cai na avenida", "Moradores sem luz há 10 horas"):
        assert mc.menciona_energia(texto), texto
    # Ampliado: plural e palavras no meio, nome antigo, conversa do dia a dia, rede em mau estado, danos.
    for texto in ("Quedas constantes de energia em Colinas", "Interrupção programada no fornecimento de energia",
                  "A Celtins nunca resolveu", "Acabou a luz de novo no Taquari", "Bairro inteiro segue no escuro",
                  "Árvore encostando nos fios da rua 5", "Fio caído na calçada", "Energia restabelecida às 22h",
                  "Oscilação queimou a geladeira", "Reajuste de 7% na tarifa de energia", "Gato de energia flagrado"):
        assert mc.menciona_energia(texto), texto
    for texto in ("Muita energia positiva hoje!", "Postei uma foto nova", "Luz, câmera, ação",
                  "Show com muita luz e som", "Caiu na rede: vídeo viral"):
        assert not mc.menciona_energia(texto), texto


def test_analisar_grava_mencao_tema_e_municipio(con, municipios):
    conta(con, municipios, "gurupinoticias", [
        post(1, legenda="Moradores do Jardim Tocantins reclamam da Energisa: 10 horas sem energia", codigo="E1"),
        post(1, legenda="Festa junina no sábado", codigo="E2"),
    ])
    conta(con, municipios, "loja", [post(1, legenda="Falta de energia? Temos geradores!", codigo="E3")],
          prioridade="C")
    candidatos = mc.candidatos(con)
    assert [p["post_id"] for p in candidatos] == ["id-E1"]  # sem palavra-chave ou conta não monitorada: fora
    assert "cobre: Gurupi" in mc.descrever(candidatos[0])

    cliente, chamadas = deepseek_falso(lambda _: resposta_deepseek(analise(tema="inventado")))
    assert mc.analisar(con, cliente, candidatos, aviso=lambda _: None) == (1, 0)
    linha = con.execute("SELECT * FROM mencoes").fetchone()
    assert linha["sobre_energia"] == 1 and linha["cita_energisa"] == 1 and linha["tema"] == "outro"
    assert con.execute("SELECT COUNT(*) FROM mencao_municipio").fetchone()[0] == 1
    assert chamadas[0]["response_format"] == {"type": "json_object"}
    assert mc.candidatos(con) == []  # já analisado


def test_reacoes_leem_comentarios_e_nao_repetem_coleta(con, municipios):
    conta(con, municipios, "gurupinoticias", [
        post(1, comentarios=40, legenda="Queda de energia no centro", codigo="R1"),
        post(1, comentarios=3, legenda="Desligamento programado na quinta", codigo="R2"),
    ])
    cliente, _ = deepseek_falso(lambda _: resposta_deepseek(analise()))
    mc.analisar(con, cliente, mc.candidatos(con), aviso=lambda _: None)

    fila = mc.sem_comentarios(con, 10)
    assert [p["shortcode"] for p in fila] == ["R1", "R2"]  # mais interação primeiro
    itens = [{"postUrl": "https://www.instagram.com/p/R1/", "id": "c1", "text": "Toda semana cai!", "likesCount": 7},
             {"postUrl": "https://www.instagram.com/p/OUTRO/", "id": "c2", "text": "?"}]
    apify = ApifyFalso({"apify/instagram-comment-scraper": itens})
    assert mon.coletar_comentarios(con, apify, fila, 15, aviso=lambda _: None) == 1
    mc.marcar_comentarios_lidos(con, fila)
    assert mc.sem_comentarios(con, 10) == []  # R2 veio sem comentários, mas não volta à fila

    pendentes = mc.reacao_pendente(con)
    assert [p["shortcode"] for p in pendentes] == ["R1"]
    cliente, chamadas = deepseek_falso(lambda _: resposta_deepseek({"reacao": "negativa", "resumo": "Reclamam."}))
    assert mc.analisar_reacoes(con, cliente, pendentes, aviso=lambda _: None) == (1, 0)
    assert "Toda semana cai!" in chamadas[0]["messages"][1]["content"]
    linha = con.execute("SELECT reacao, n_comentarios_lidos FROM mencoes WHERE post_id = 'id-R1'").fetchone()
    assert tuple(linha) == ("negativa", 1)


def test_relatorio_engajamento_e_repercussao(con, municipios, tmp_path):
    conta(con, municipios, "gurupinoticias", [post(1, curtidas=900, comentarios=100, legenda="Apagão", codigo="G1")])
    conta(con, municipios, "gurupialerta", [post(2, curtidas=90, comentarios=10, legenda="Sem energia", codigo="G2")],
          seguidores=1_000)
    conta(con, municipios, "palmasagora", [post(1, legenda="Falta de energia em Palmas", codigo="P1"),
                                           post(1, legenda="Energisa reajusta a tarifa", codigo="P2")],
          municipios=["Palmas"], abrangencia="estadual")

    def responder(params):
        texto = params["messages"][1]["content"]
        if "tarifa" in texto:
            return resposta_deepseek(analise(tema="conta_tarifa", alcance="estadual", municipios=[]))
        return resposta_deepseek(analise(municipios=["Palmas"]) if "@palmasagora" in texto else analise())

    cliente, _ = deepseek_falso(responder)
    mc.analisar(con, cliente, mc.candidatos(con), aviso=lambda _: None)

    df = relatorio.carregar(con).set_index(["conta", "tema"])
    assert df.loc[("gurupinoticias", "falta_energia"), "interacoes"] == 1000
    assert df.loc[("gurupinoticias", "falta_energia"), "engajamento_pct"] == 10.0  # 1000 / 10 mil seguidores
    # Mesmo tema e município, 1 dia de diferença: as duas contas de Gurupi repercutiram o mesmo fato.
    assert df.loc[("gurupialerta", "falta_energia"), "contas_que_repercutiram"] == 2
    assert df.loc[("palmasagora", "falta_energia"), "contas_que_repercutiram"] == 1
    assert df.loc[("palmasagora", "falta_energia"), "teve_cobertura_estadual"] == "sim"
    assert df.loc[("gurupinoticias", "falta_energia"), "teve_cobertura_estadual"] == "não"
    assert df.loc[("palmasagora", "conta_tarifa"), "municipios"] == ""

    caminho, n = relatorio.exportar(con, tmp_path / "mencoes.xlsx")
    livro = load_workbook(caminho)
    assert n == 4 and livro.sheetnames == ["Menções", "Por tema", "Por setor", "Por município", "Sentimento x alcance",
                                           "Comentários", "Mesma notícia"]
    por_municipio = {l[0]: l[1] for l in livro["Por município"].iter_rows(min_row=2, values_only=True)}
    assert por_municipio == {"Gurupi": 2, "Palmas": 1, "(não identificado)": 1}


def test_relatorio_vazio(con, tmp_path):
    caminho, n = relatorio.exportar(con, tmp_path / "vazio.xlsx")
    assert n == 0 and caminho.exists()


def test_historico_coleta_o_periodo_e_nao_repete(con, municipios):
    conta(con, municipios, "portal", [post(d // 2, codigo=f"P{d}") for d in range(12)])  # 12 posts em 5 dias
    conta(con, municipios, "pequeno", [post(30, codigo="Q1")])
    assert abs(mon.ritmo(con, "portal") - 2.4) < 0.1 and mon.ritmo(con, "pequeno") == 0
    contas = mon.monitoradas(con)
    estimativa = mon.estimar_historico(con, contas, 60)
    assert estimativa == {"portal": 144, "pequeno": 0}

    antigos = [post(40, legenda="Apagão no centro", codigo="H1") | {"ownerUsername": "Portal"},
               post(20, codigo="H2") | {"ownerUsername": "portal"}]
    apify = ApifyFalso({config.ATOR_POSTS: antigos})
    r = mon.coletar_historico(con, apify, contas, "2026-09-02", estimativa, aviso=lambda _: None)
    assert r["contas"] == 2 and r["posts"] == 2
    entrada = apify.chamadas[0][1]
    assert sorted(entrada.pop("username")) == ["pequeno", "portal"]  # mesmo score: a ordem não importa
    assert entrada == {"onlyPostsNewerThan": "2026-09-02", "resultsLimit": 318}
    assert con.execute("SELECT COUNT(*) FROM posts WHERE post_id IN ('id-H1', 'id-H2')").fetchone()[0] == 2
    assert dict(con.execute("SELECT username, n_posts FROM historico").fetchall()) == {"portal": 2, "pequeno": 0}
    assert mon.sem_historico(con, contas, "2026-09-02") == []
    assert len(mon.sem_historico(con, contas, "2026-08-01")) == 2
    assert [p["post_id"] for p in mc.candidatos(con)] == ["id-H1"]  # o post antigo entra na análise de menções


def test_historico_que_bate_no_limite_so_cobre_ate_o_post_mais_antigo(con, municipios):
    conta(con, municipios, "portal", [])
    itens = [post(3, codigo="A") | {"ownerUsername": "portal"}, post(5, codigo="B") | {"ownerUsername": "portal"}]
    mon.ingerir_historico(con, itens, ["portal"], "2026-01-01", limite=2)
    desde = con.execute("SELECT desde FROM historico").fetchone()[0]
    assert desde == itens[1]["timestamp"][:10] and desde > "2026-01-01"


def test_historico_respeita_o_teto_de_gasto(con, municipios, monkeypatch):
    monkeypatch.setattr(mon, "CONTAS_POR_LOTE_HISTORICO", 1)
    conta(con, municipios, "a", [])
    conta(con, municipios, "b", [])
    apify = ApifyFalso({config.ATOR_POSTS: []}, custo_por_run=0.6)
    r = mon.coletar_historico(con, apify, mon.monitoradas(con), "2026-09-02", {"a": 10, "b": 10}, max_usd=0.5,
                              aviso=lambda _: None)
    assert r["contas"] == 1 and len(apify.chamadas) == 1


def test_setor_da_energisa_vem_da_ia_ou_do_tema():
    assert mc.validar(analise(setor="manutencao"))["setor"] == "manutencao"
    # Setor fora da lista: vale o mais comum do tema.
    assert mc.validar(analise(tema="conta_tarifa", setor="financeiro"))["setor"] == "comercial"
    assert mc.validar(analise(tema="falta_energia"))["setor"] == "operacao"
    assert set(mc.SETOR_DO_TEMA) == set(mc.TEMAS) and set(mc.SETOR_DO_TEMA.values()) <= set(mc.SETORES)
    assert "setor:" in mc.INSTRUCOES and "rh:" in mc.INSTRUCOES


def test_completar_setor_das_mencoes_antigas(con, municipios, tmp_path):
    conta(con, municipios, "gurupinoticias", [post(1, legenda="Energisa demora 3 dias para religar", codigo="S1")])
    cliente, _ = deepseek_falso(lambda _: resposta_deepseek(analise(tema="atendimento", setor="atendimento")))
    mc.analisar(con, cliente, mc.candidatos(con), aviso=lambda _: None)
    con.execute("UPDATE mencoes SET setor = NULL")  # menção analisada antes de existir o setor
    assert [p["post_id"] for p in mc.candidatos(con, sem_setor=True)] == ["id-S1"]
    cliente, _ = deepseek_falso(lambda _: resposta_deepseek(analise(tema="atendimento", setor="manutencao")))
    mc.analisar(con, cliente, mc.candidatos(con, sem_setor=True), aviso=lambda _: None)
    assert con.execute("SELECT setor FROM mencoes").fetchone()[0] == "manutencao"
    assert mc.candidatos(con, sem_setor=True) == []
    caminho, _ = relatorio.exportar(con, tmp_path / "m.xlsx")
    planilha = load_workbook(caminho)
    assert "Por setor" in planilha.sheetnames
    cabecalho = [c.value for c in planilha["Menções"][1]]
    assert planilha["Menções"].cell(2, cabecalho.index("setor") + 1).value == "manutencao"
