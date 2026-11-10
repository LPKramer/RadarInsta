from radar import app_dados, localizacao as lo


def _ibge(municipios, nome):
    return next(m["ibge_id"] for m in municipios if m["nome"] == nome)


def test_municipio_no_texto_so_com_inicial_maiuscula(municipios):
    achar = lo.localizador(municipios)
    nomes = {m["ibge_id"]: m["nome"] for m in municipios}
    assert achar("Salva de palmas para a equipe") is None  # palavra comum, não a capital
    assert nomes[achar("Acidente na BR-153 em Gurupi deixa feridos")] == "Gurupi"
    assert nomes[achar("PALMAS: prefeitura anuncia obra")] == "Palmas"
    assert nomes[achar("Moradores de Porto Nacional reclamam")] == "Porto Nacional"  # o nome inteiro, não "Porto"
    assert nomes[achar("Festa em Paraíso do Tocantins e em Gurupi")] == "Paraíso do Tocantins"  # o primeiro citado
    assert nomes[achar("#PortoNacional")] == "Porto Nacional"
    # Na marcação de local do post vale também em minúsculas: é sempre nome de lugar.
    assert nomes[achar("palmas - to", maiuscula=False)] == "Palmas"
    assert achar(None) is None and achar("Tocantins") is None


def test_ordem_das_pistas_do_post(municipios):
    achar = lo.localizador(municipios)
    gurupi, palmas, araguaina = (_ibge(municipios, n) for n in ("Gurupi", "Palmas", "Araguaína"))
    cidades = {"portal": araguaina}
    assert lo.do_post("1", "Obra em Palmas", "Gurupi - TO", "portal", {"1": gurupi}, cidades, achar) == (gurupi, "fato")
    assert lo.do_post("2", "Obra em Palmas", "Gurupi - TO", "portal", {}, cidades, achar) == (gurupi, "marcacao")
    assert lo.do_post("3", "Obra em Palmas", "Tocantins", "portal", {}, cidades, achar) == (palmas, "texto")
    assert lo.do_post("4", "Bom dia!", None, "portal", {}, cidades, achar) == (araguaina, "conta")
    assert lo.do_post("5", "Bom dia!", None, "outra", {}, cidades, achar) is None


def test_cidade_da_conta(con, municipios):
    gurupi, palmas, peixe = (_ibge(municipios, n) for n in ("Gurupi", "Palmas", "Peixe"))
    linhas = [("local", gurupi, "ia"), ("pequena", peixe, "ia"), ("pequena", palmas, "ia"), ("pequena", peixe, "bio"),
              ("grande", palmas, "ia"), ("grande", gurupi, "ia")]
    linhas += [("estadual", m["ibge_id"], "ia") for m in municipios[:5]]
    con.executemany("INSERT INTO conta_municipio (rede, username, ibge_id, fonte) VALUES ('instagram', ?, ?, ?)", linhas)
    cidades = lo.cidades_das_contas(con, municipios)
    assert cidades["local"] == gurupi
    assert cidades["pequena"] == peixe  # a IA citou a vizinha grande, mas a bio fala de Peixe
    assert cidades["grande"] == palmas  # sem outra pista, a maior
    assert "estadual" not in cidades  # ligada a mais de três municípios: fica fora do mapa


def test_centro_fica_dentro_do_municipio_e_os_pontos_se_espalham():
    geo = app_dados.mapa()
    for d in geo["paths"].values():
        x, y = lo.centro(d)
        assert lo.dentro(x, y, max(lo.aneis(d), key=len))
    pontos = lo.espalhar(30, 2.5)
    assert pontos[0] == (0.0, 0.0)
    distancias = [((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for i, a in enumerate(pontos) for b in pontos[i + 1:]]
    assert min(distancias) > 2.0  # sem ponto em cima de ponto
