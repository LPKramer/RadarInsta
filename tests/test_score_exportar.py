from openpyxl import load_workbook

from radar import classificacao as cl, exportar as exp, perfis, score as sc

from conftest import classificacao, perfil, post


def calcular(seguidores=20_000, classif=None, tem_municipio=True, **extra):
    conta = perfis.normalizar(perfil("x", followersCount=seguidores, **extra))
    return sc.calcular(conta, classif or classificacao(), tem_municipio)


def test_score_cresce_com_alcance_e_atividade():
    assert calcular(100_000)[0] > calcular(2_000)[0]
    ativo = calcular(latestPosts=[post(d) for d in range(30)])
    parado = calcular(latestPosts=[post(25)])
    assert ativo[0] > parado[0]


def test_prioridades():
    assert calcular(100_000, latestPosts=[post(d) for d in range(20)])[1] == "A"
    irrelevante = classificacao(tipo="irrelevante", publica_noticias_locais=False)
    assert calcular(1_000_000, irrelevante)[1] == "C"
    # Qualquer tipo só conta se publica notícia local (ex.: fofoca de celebridades nacionais não serve).
    influ = classificacao(tipo="influenciador_local", publica_noticias_locais=False)
    assert calcular(100_000, influ)[1] == "C"
    assert calcular(100_000, influ | {"publica_noticias_locais": True})[1] in ("A", "B")
    fofoca_nacional = classificacao(tipo="perfil_fofoca", publica_noticias_locais=False, abrangencia="nacional")
    assert calcular(100_000, fofoca_nacional)[1] == "C"
    # Notícia local sem nenhum município do Tocantins é de outro estado (ex.: prefeitura de Goiás).
    outro_estado = classificacao(tipo="orgao_publico", municipios=[])
    assert calcular(100_000, outro_estado, tem_municipio=False)[1] == "C"
    estadual = classificacao(abrangencia="estadual", municipios=[])
    assert calcular(100_000, estadual, tem_municipio=False, latestPosts=[post(d) for d in range(20)])[1] == "A"


def test_foco_fora_do_estado_rebaixa():
    nacional = classificacao(abrangencia="nacional", municipios=[])
    assert calcular(classif=nacional, tem_municipio=False)[0] < calcular()[0]


def preparar(con, municipios):
    perfis.ingerir(con, perfil("gurupinoticias", followersCount=50_000), "busca", municipios)
    perfis.ingerir(con, perfil("lojinha"), "busca", municipios)
    cl.salvar(con, "gurupinoticias", classificacao(), municipios, "m")
    cl.salvar(con, "lojinha", classificacao(tipo="irrelevante", publica_noticias_locais=False,
                                            municipios=[]), municipios, "m")
    return sc.pontuar(con)


def test_pontuar(con, municipios):
    contagem = preparar(con, municipios)
    assert sum(contagem.values()) == 2 and contagem["C"] >= 1


def test_exportar_e_importar_revisao(con, municipios, tmp_path):
    preparar(con, municipios)
    caminho, n = exp.exportar(con, tmp_path / "contas.xlsx")
    assert n == 2

    livro = load_workbook(caminho)
    aba = livro[exp.ABA_CONTAS]
    colunas = {c.value: c.column_letter for c in aba[1]}
    assert aba[f"{colunas['username']}2"].value == "gurupinoticias"  # prioridade mais alta primeiro
    assert aba[f"{colunas['municipios_ia']}2"].value == "Gurupi"
    cobertura = livro[exp.ABA_MUNICIPIOS]
    assert cobertura.max_row == 140  # cabeçalho + 139 municípios

    aba[f"{colunas['status']}2"] = "Aprovada"
    aba[f"{colunas['status']}3"] = "talvez"
    aba[f"{colunas['nota']}2"] = "Fonte boa"
    livro.save(caminho)

    resumo = exp.importar_revisao(con, caminho)
    assert resumo["aprovada"] == 1 and resumo["invalidas"] == 1
    linha = con.execute("SELECT * FROM revisao WHERE username = 'gurupinoticias'").fetchone()
    assert linha["status"] == "aprovada" and linha["nota"] == "Fonte boa" and linha["revisada_em"]

    # Reexportar mantém a decisão da equipe.
    caminho, _ = exp.exportar(con, tmp_path / "contas2.xlsx")
    aba = load_workbook(caminho)[exp.ABA_CONTAS]
    assert aba[f"{colunas['status']}2"].value == "aprovada"
