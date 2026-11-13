import io
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest
from PIL import Image

from radar import capas, db, mencoes as mc, municipios as mun, painel

from conftest import analise, conta, deepseek_falso, post, resposta_deepseek


@pytest.fixture(autouse=True)
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(capas, "PASTA", tmp_path / "capas")
    monkeypatch.setattr(capas, "STORIES", tmp_path / "stories")
    return tmp_path / "capas"


def _jpeg() -> bytes:
    saida = io.BytesIO()
    Image.new("RGB", (800, 1000), (30, 90, 160)).save(saida, "JPEG")
    return saida.getvalue()


def _base(con, municipios):
    """Um portal de Gurupi com uma menção negativa (apagão), um post comum, um reel, um story qualquer (s1) e um
    story sobre a Energisa (s2), com a imagem que o coletor salvou para o OCR."""
    conta(con, municipios, "gurupinoticias", [
        post(1, curtidas=900, comentarios=100, legenda="🚨 URGENTE: Apagão deixa Gurupi no escuro #gurupi", codigo="G1"),
        post(2, curtidas=50, comentarios=5, legenda="Festa na praça", codigo="G2"),
        post(1, curtidas=10, comentarios=1, legenda="Reel da feira", codigo="G3") | {"type": "Video",
                                                                                    "videoViewCount": 40000},
    ], seguidores=20_000)
    cliente, _ = deepseek_falso(lambda _: resposta_deepseek(analise()))
    mc.analisar(con, cliente, mc.candidatos(con), aviso=lambda _: None)
    uma_hora = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    con.execute("INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, medido_em) VALUES "
                "('instagram', 's1', 'gurupinoticias', 'https://www.instagram.com/stories/gurupinoticias/s1/', "
                "'Story', ?, 'texto do ocr', 'x')", (uma_hora,))
    imagem = capas.STORIES / "gurupinoticias" / "s2.jpg"
    imagem.parent.mkdir(parents=True, exist_ok=True)
    imagem.write_bytes(_jpeg())
    meia_hora = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    con.execute("INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, medido_em, arquivo) "
                "VALUES ('instagram', 's2', 'gurupinoticias', 'https://www.instagram.com/stories/gurupinoticias/s2/', "
                "'Story', ?, 'Falta de luz no centro de Gurupi, cadê a Energisa?', 'x', ?)",
                (meia_hora, str(imagem.with_suffix(".mp4"))))
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, tema, sentimento, alcance, resumo, "
                "analisada_em) VALUES ('instagram', 's2', 1, 1, 'falta_energia', 'neutro', 'local', "
                "'Story cobra a volta da luz no centro.', 'x')")
    con.commit()


def test_titulo_limpa_emojis_hashtags_e_links():
    assert painel.titulo("🚨 URGENTE: Apagão deixa Gurupi no escuro #gurupi @fulano https://t.co/x") == \
        "URGENTE: Apagão deixa Gurupi no escuro"
    assert painel.titulo("#só #hashtag\n\nSegunda linha com texto") == "Segunda linha com texto"
    assert painel.titulo(None) == ""
    assert painel.titulo("palavra " * 40, limite=30).endswith("…")


def test_inicio_tem_destaque_fileiras_e_top10(con, municipios):
    _base(con, municipios)
    dados = painel.inicio(con)
    destaque = dados["destaques"][0]
    assert destaque["id"] == "id-G1" and destaque["mencao"]["sentimento"] == "negativo"
    assert destaque["municipios"] == ["Gurupi"] and destaque["titulo"] == "URGENTE: Apagão deixa Gurupi no escuro"
    linhas = {l["id"]: l for l in dados["linhas"]}
    assert [p["id"] for p in linhas["mencoes"]["itens"]] == ["s2", "id-G1"]
    assert linhas["top10"]["itens"][0]["id"] == "id-G1"
    assert all(p["formato"] != "Story" for p in linhas["top10"]["itens"])
    # Só o story sobre a Energisa aparece, com a capa feita da imagem salva para o OCR (o vídeo tem a .jpg ao lado).
    assert [p["id"] for p in linhas["stories"]["itens"]] == ["s2"] and linhas["stories"]["itens"][0]["capa"] is True
    assert "s1" not in {p.get("id") for l in dados["linhas"] for p in l["itens"]}
    assert "reels" not in linhas  # o reel da feira não fala de energia: não aparece em lugar nenhum
    # Só menções na tela: o post da festa e o reel da feira ficam no banco, fora das fileiras.
    assert {p["id"] for l in dados["linhas"] if l["formato"] != "conta" for p in l["itens"]} == {"id-G1", "s2"}
    assert "recentes" not in linhas  # a aba Recentes não vira fileira: repetiria "Menções à Energisa"
    assert [p["id"] for p in linhas["diretas"]["itens"]] == ["s2"]  # o story cita a Energisa
    assert [p["id"] for p in linhas["indiretas"]["itens"]] == ["id-G1"]  # o apagão não cita, mas é de energia
    assert linhas["negativas"]["verTudo"] == "ver/mencoes?sentimento=negativo"
    assert linhas["contas"]["verTudo"] == "contas?ordem=mencoes"
    assert linhas["contas"]["itens"][0]["username"] == "gurupinoticias"
    assert any(l["titulo"] == "Gurupi" for l in dados["linhas"])  # fileira da cidade


def test_ficha_do_post_traz_analise_repercussao_e_mais_da_conta(con, municipios):
    _base(con, municipios)
    p = painel.post(con, "id-G1")
    assert p["mencao"]["municipios"] == ["Gurupi"] and p["mencao"]["repercutiram"] == 1
    assert p["mencao"]["coberturaEstadual"] is False
    assert p["engajamentoPct"] == 5.0  # 1.000 interações ÷ 20 mil seguidores
    assert p["contaInfo"]["username"] == "gurupinoticias" and p["contaInfo"]["municipios"] == ["Gurupi"]
    assert [x["id"] for x in p["maisDaConta"]] == ["s2"]  # só o que a conta publicou sobre a Energisa
    assert painel.post(con, "id-G2") is None  # post fora da Energisa não abre nem por endereço direto
    assert p["capa"] is False and p["midia"] is None
    assert p["onde"]["nome"] == "Gurupi" and p["onde"]["fonte"] == "fato"  # o município que a IA apontou
    assert painel.post(con, "nao-existe") is None
    assert painel.post(con, "s1") is None  # story fora da Energisa não abre nem por endereço direto
    story = painel.post(con, "s2")
    assert story["capa"] is True and story["onde"]["nome"] == "Gurupi" and story["onde"]["fonte"] == "texto"


def test_filtros_da_pagina_ver_tudo(con, municipios):
    _base(con, municipios)
    assert painel.linha(con, "mencoes", sobrepor={"sentimento": "positivo"})["itens"] == []
    negativas = painel.linha(con, "mencoes", sobrepor={"sentimento": "negativo", "dias": "todos"})
    assert [p["id"] for p in negativas["itens"]] == ["id-G1"] and negativas["filtros"]["dias"] is None
    estranho = painel.linha(con, "reels", sobrepor={"dias": "abc", "sentimento": "x' OR 1=1", "ordem": "nenhuma"})
    assert estranho["filtros"]["dias"] is None and estranho["filtros"]["sentimento"] is None
    assert painel.linha(con, "nao-existe") is None and painel.linha(con, "conta-../x") is None
    gurupi = next(i for i, nome in painel._cidades(con) if nome == "Gurupi")
    assert painel.linha(con, f"cidade-{gurupi}")["titulo"] == "Contas de Gurupi"


def mesma_noticia(con, *post_ids: str) -> int:
    """Liga as menções num agrupamento da mesma notícia, como o fatos.consolidar faria."""
    fato_id = con.execute("INSERT INTO fatos (titulo, primeira_em, ultima_em, criado_em, atualizado_em) "
                          "VALUES ('x', 'x', 'x', 'x', 'x')").lastrowid
    for post_id in post_ids:
        con.execute("INSERT INTO fato_mencao (rede, post_id, fato_id, como, ligado_em) "
                    "VALUES ('instagram', ?, ?, 'manual', 'x')", (post_id, fato_id))
    con.commit()
    return fato_id


def test_fileiras_e_ver_tudo_mostram_cada_noticia_uma_vez(con, municipios):
    _base(con, municipios)
    # Outro portal deu o mesmo apagão hoje: as duas menções são a mesma notícia.
    conta(con, municipios, "outroportal", [post(0, curtidas=40, comentarios=4, legenda="Apagão em Gurupi", codigo="O1")])
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, tema, sentimento, alcance, resumo, "
                "analisada_em) VALUES ('instagram', 'id-O1', 1, 0, 'falta_energia', 'negativo', 'local', "
                "'Apagão em Gurupi.', 'x')")
    mesma_noticia(con, "id-G1", "id-O1")
    # Mais novas primeiro: o apagão uma vez só, no post mais novo (o do outro portal, de agora), com as somas; depois
    # o story, de meia hora atrás.
    mencoes = painel.linha(con, "mencoes")
    assert [p["id"] for p in mencoes["itens"]] == ["id-O1", "s2"] and "noticia" not in mencoes["itens"][1]
    noticia = mencoes["itens"][0]["noticia"]
    assert (noticia["posts"], noticia["perfis"], noticia["interacoes"]) == (2, 2, 1044)
    # Por interações, o cartão é o post de mais interações da notícia.
    por_interacao = painel.linha(con, "mencoes", sobrepor={"ordem": "interacoes"})
    assert [p["id"] for p in por_interacao["itens"]] == ["id-G1", "s2"]
    # O "ver tudo" pagina notícias, não posts; a tela inicial e a cidade também juntam.
    paginas = [painel.linha(con, "mencoes", pagina, limite=1) for pagina in range(3)]
    assert [([p["id"] for p in d["itens"]], d["mais"]) for d in paginas] == [
        (["id-O1"], True), (["s2"], False), ([], False)]
    linhas = {l["id"]: l for l in painel.inicio(con)["linhas"]}
    assert [p["id"] for p in linhas["mencoes"]["itens"]] == ["id-O1", "s2"]
    assert [p["id"] for p in linhas["negativas"]["itens"]] == ["id-O1"]
    # A busca mostra cada post, já com a mesma notícia anotada; a ficha continua com todos os posts.
    achados = {p["id"]: p for p in painel.busca(con, "apagao")["posts"]}
    assert set(achados) == {"id-G1", "id-O1"} and achados["id-G1"]["noticia"] == noticia
    assert [p["id"] for p in painel.post(con, "id-O1")["mesmaNoticia"]] == ["id-G1"]


def test_aba_recentes_so_tem_mencoes_em_periodos_curtos(con, municipios):
    _base(con, municipios)
    padrao = painel.linha(con, "recentes")
    assert padrao["periodos"] == [1, 3, 7] and padrao["filtros"] == {"dias": 1, "sentimento": None, "ordem": "recentes"}
    assert "periodos" not in painel.linha(con, "mencoes")
    # Período fora dos dela (inclusive "todos") e sentimento não mudam nada: fica o padrão.
    for sobrepor in ({"dias": "todos"}, {"dias": "30"}, {"dias": "abc"}, {"sentimento": "negativo"}):
        assert painel.linha(con, "recentes", sobrepor=sobrepor)["filtros"] == padrao["filtros"], sobrepor
    # Só o que tem a ver com a Energisa, stories inclusive: o story da Energisa e o apagão. A festa, o reel da feira e
    # o story qualquer, não.
    tres = painel.linha(con, "recentes", sobrepor={"dias": "3"})
    assert [p["id"] for p in tres["itens"]] == ["s2", "id-G1"] and tres["filtros"]["dias"] == 3
    interacoes = painel.linha(con, "recentes", sobrepor={"dias": "7", "ordem": "interacoes"})
    assert [p["id"] for p in interacoes["itens"]] == ["id-G1", "s2"]


def test_busca_ignora_acentos_e_status_conta_novidades(con, municipios):
    _base(con, municipios)
    assert [p["id"] for p in painel.busca(con, "apagao")["posts"]] == ["id-G1"]
    assert painel.busca(con, "ocr")["posts"] == []  # o story s1 não é da Energisa
    assert [p["id"] for p in painel.busca(con, "cade a energisa")["posts"]] == ["s2"]
    assert painel.busca(con, "gurupinot")["contas"][0]["username"] == "gurupinoticias"
    assert painel.busca(con, "a")["posts"] == [] and painel.busca(con, "100%")["posts"] == []
    estado = painel.status(con)
    assert estado["stories24h"] == 1 and estado["mencoes"] == 2 and estado["novos"] == 0
    assert estado["aoVivo"] is False  # nenhuma visita registrada
    con.execute("INSERT INTO posts (rede, post_id, username, medido_em) VALUES ('instagram', 'n', 'gurupinoticias', 'x')")
    con.execute("INSERT INTO posts (rede, post_id, username, tipo, medido_em) "
                "VALUES ('instagram', 'n2', 'gurupinoticias', 'Story', 'x')")
    assert painel.status(con, apos=estado["ultimoId"])["novos"] == 0  # post comum e story de fora não contam
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, analisada_em) "
                "VALUES ('instagram', 'n', 1, 0, 'x')")
    assert painel.status(con, apos=estado["ultimoId"])["novos"] == 1  # a IA ligou o post à Energisa


def test_contas_municipios_e_pagina_da_cidade(con, municipios):
    _base(con, municipios)
    lista = painel.lista_contas(con)
    assert lista["itens"][0]["username"] == "gurupinoticias" and lista["tipos"] == ["portal_noticias"]
    gurupi = next(i for i, nome in painel._cidades(con) if nome == "Gurupi")
    pagina = painel.municipio(con, gurupi)
    assert [l["titulo"] for l in pagina["linhas"]] == ["Menções à Energisa", "Publicadas pelas contas da cidade",
                                                      "Contas da cidade que falam da Energisa"]
    assert pagina["mencoes"] == 1 and pagina["d"].startswith("M")
    assert painel.municipio(con, 1) is None
    c = painel.conta(con, "gurupinoticias")
    assert c["monitorada"] and c["municipios"] == ["Gurupi"] and [p["id"] for p in c["posts"]["itens"]] == ["id-G1"]
    assert [l["titulo"] for l in c["linhas"]] == ["Stories sobre a Energisa"] and (c["mencoes"], c["negativas"]) == (2, 1)
    assert c["onde"]["nome"] == "Gurupi" and c["onde"]["municipios"] == [gurupi]


def test_servidor_entrega_api_capas_e_nao_sai_das_pastas(tmp_path):
    caminho = tmp_path / "radar.db"
    con = db.conectar(caminho)
    mun.salvar(con, mun.ler_csv())
    _base(con, mun.carregar(con))
    # Matéria de site: o id é o endereço dela, com a barra no fim.
    materia = "https://jornal.exemplo.com.br/tocantins/2026/11/conta-de-luz/"
    con.execute("INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, medido_em) VALUES "
                "('web', ?, 'jornal.exemplo.com.br', ?, 'Materia', '2026-11-10T12:00:00Z', 'Conta de luz sobe', 'x')",
                (materia, materia))
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, sentimento, analisada_em) "
                "VALUES ('web', ?, 1, 1, 'negativo', 'x')", (materia,))
    con.commit()
    con.close()
    assert capas.guardar(_jpeg(), capas.arquivo_post("id-G1"), capas.LARGURA_POST)

    painel.Painel.caminho_banco = caminho
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), painel.Painel)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{servidor.server_address[1]}"

    def pedir(rota):
        try:
            with urllib.request.urlopen(base + rota, timeout=10) as resposta:
                return resposta.status, resposta.headers.get("Content-Type"), resposta.read()
        except urllib.error.HTTPError as erro:
            return erro.code, erro.headers.get("Content-Type"), erro.read()

    try:
        codigo, _, corpo = pedir("/api/inicio")
        assert codigo == 200 and json.loads(corpo)["destaques"][0]["id"] == "id-G1"
        assert json.loads(pedir("/api/post/id-G1")[2])["capa"] is True
        assert pedir("/capa/id-G1")[:2] == (200, "image/jpeg")
        assert pedir("/capa/id-G2")[0] == 404  # sem arquivo nem endereço
        assert pedir("/capa/s2")[:2] == (200, "image/jpeg")  # story da Energisa: da imagem salva para o OCR
        assert pedir("/api/post/s1")[0] == 404
        codigo, _, corpo = pedir("/api/post/" + urllib.parse.quote(materia, safe=""))  # como o app.js pede (enc)
        assert codigo == 200 and json.loads(corpo)["id"] == materia
        assert pedir("/marca/energisa.svg")[1] == "image/svg+xml"
        mapa = json.loads(pedir("/api/mapa?camada=mencoes")[2])
        assert {p["id"] for p in mapa["pontos"]} == {"id-G1", "s2"} and mapa["municipios"][0]["nome"] == "Gurupi"
        assert json.loads(pedir("/api/mapa?camada=x&dias=nan")[2])["camada"] == "mencoes"
        assert pedir("/api/post/id-G2")[0] == 404
        assert len(json.loads(pedir("/api/mapa-base")[2])["municipios"]) == 139
        assert pedir("/")[1].startswith("text/html") and pedir("/app.js")[0] == 200
        assert pedir("/fontes/BarlowCondensed-ExtraBold.ttf")[1] == "font/ttf"
        for rota in ("/../radar/db.py", "/%2e%2e/radar/db.py", "/capa/..%2F..%2Fradar.db", "/midia/id-G1",
                     "/api/post/nada", "/api/linha/conta-..%2Fx"):
            assert pedir(rota)[0] == 404, rota
        assert json.loads(pedir("/api/linha/mencoes?dias=todos&sentimento=negativo")[2])["itens"][0]["id"] == "id-G1"
        assert json.loads(pedir("/api/busca?q=apag%C3%A3o")[2])["posts"][0]["id"] == "id-G1"
    finally:
        servidor.shutdown()
        servidor.server_close()
        painel.Painel.caminho_banco = None


def test_mapa_poe_cada_item_no_seu_municipio(con, municipios):
    _base(con, municipios)
    mencoes = painel.mapa(con, "noticias")  # camada que não existe mais: vale a das menções
    # O apagão fica no município que a IA apontou; o story, no citado no texto. Os posts comuns não entram.
    assert mencoes["camada"] == "mencoes"
    assert [(p["id"], p["fonte"]) for p in mencoes["pontos"]] == [("id-G1", "fato"), ("s2", "texto")]
    assert mencoes["municipios"][0]["nome"] == "Gurupi" and mencoes["total"] == 2 and mencoes["semLocal"] == 0
    primeiro, segundo = mencoes["pontos"][:2]
    assert (primeiro["x"], primeiro["y"]) != (segundo["x"], segundo["y"])  # espalhados em volta do centro
    assert [p["id"] for p in painel.mapa(con, "mencoes", sentimento="neutro")["pontos"]] == ["s2"]
    perfis = painel.mapa(con, "perfis")
    assert [(p["username"], p["mencoes"]) for p in perfis["pontos"]] == [("gurupinoticias", 2)]
    base = painel.mapa_base(con)
    gurupi = next(m for m in base["municipios"] if m["nome"] == "Gurupi")
    assert 0 < gurupi["x"] < base["largura"] and 0 < gurupi["y"] < base["altura"]


def test_mapa_da_base_tem_as_contas_que_a_ia_ligou_ao_to(con, municipios):
    _base(con, municipios)
    por_nome = {m["nome"]: m for m in municipios}
    # Prioridade C: fora do monitoramento. A IA pôs a conta em Araguaína.
    conta(con, municipios, "vozdearaguaina", [], prioridade="C", seguidores=50_000, municipios=["Araguaína"])
    conta(con, municipios, "lojadearaguaina", [], prioridade="C", municipios=["Araguaína"])
    con.execute("UPDATE contas SET descartada = 'comercial' WHERE username = 'lojadearaguaina'")
    conta(con, municipios, "bardearaguaina", [], prioridade="C", tipo="irrelevante", municipios=["Araguaína"])
    # A busca por "Sampaio" achou um ator de fora; a IA não o ligou a município nenhum do TO.
    conta(con, municipios, "atorsampaio", [], prioridade="C", seguidores=1_500_000, tipo="influenciador_local",
          abrangencia="nacional", municipios=[])
    con.execute("INSERT INTO conta_municipio (rede, username, ibge_id, fonte) VALUES ('instagram', 'atorsampaio', ?, "
                "'busca')", (por_nome["Sampaio"]["ibge_id"],))
    conta(con, municipios, "portaldoto", [], prioridade="C", abrangencia="estadual", municipios=[])
    con.commit()
    dados = painel.mapa(con, "base")
    # A conta da menção continua; a nova entra mesmo sem falar da Energisa; a descartada, a irrelevante e a de fora
    # não; a estadual, sem cidade, só entra na contagem.
    assert [(p["username"], p["mencoes"], p["monitorada"]) for p in dados["pontos"]] == [
        ("vozdearaguaina", 0, False), ("gurupinoticias", 2, True)]
    assert [m["nome"] for m in dados["municipios"]] == ["Araguaína", "Gurupi"]
    assert dados["total"] == 2 and dados["semLocal"] == 1
    total = sum(m["populacao"] or 0 for m in municipios)
    assert dados["populacao"] == round((por_nome["Araguaína"]["populacao"] + por_nome["Gurupi"]["populacao"]) / total, 4)
    assert "populacao" not in painel.mapa(con, "perfis") and "monitorada" not in painel.mapa(con, "perfis")["pontos"][0]


def test_setores_separam_as_mencoes_pelo_setor_da_empresa(con, municipios):
    _base(con, municipios)
    assert set(painel.NOMES_SETOR) == set(mc.SETORES)  # um nome para cada setor que a IA pode escolher
    dados = painel.setores(con)
    # O apagão é da Operação (setor que a IA tira do tema); o story sem setor fica por último.
    assert [(s["id"], s["titulo"], s["n"], s["negativas"]) for s in dados["setores"]] == [
        ("operacao", "Operação", 1, 1), ("", "Sem setor", 1, 0)]
    assert dados["total"] == 2 and dados["setores"][0]["itens"][0]["id"] == "id-G1"
    assert [s["id"] for s in painel.setores(con, sentimento="neutro")["setores"]] == [""]
    pagina = painel.linha(con, "setor-operacao")
    assert pagina["titulo"] == "Operação" and [p["id"] for p in pagina["itens"]] == ["id-G1"]
    assert painel.linha(con, "setor-x") is None


def test_contas_traz_todas_as_monitoradas(con, municipios):
    _base(con, municipios)
    conta(con, municipios, "semmencao", [post(1, codigo="Z1", legenda="Festa junina")], seguidores=50_000)
    conta(con, municipios, "fora", [post(1, codigo="Z2", legenda="Bom dia")], prioridade="C", seguidores=90_000)
    # Monitorada que nunca falou da Energisa entra na página Contas; a que não é monitorada nem falou, não.
    lista = painel.lista_contas(con)
    assert [(c["username"], c["mencoes"]) for c in lista["itens"]] == [("semmencao", 0), ("gurupinoticias", 2)]
    assert [c["username"] for c in painel.lista_contas(con, ordem="mencoes")["itens"]] == ["gurupinoticias",
                                                                                         "semmencao"]
    assert painel.lista_contas(con, ordem="x' OR 1=1")["ordem"] == "seguidores"
    # A fileira da tela inicial continua só com quem falou da Energisa.
    linhas = {l["id"]: l for l in painel.inicio(con)["linhas"]}
    assert [c["username"] for c in linhas["contas"]["itens"]] == ["gurupinoticias"]
    assert [c["username"] for c in painel.busca(con, "semmen")["contas"]] == ["semmencao"]
    assert painel.busca(con, "fora")["contas"] == []


def test_noticias_de_sites_entram_no_painel(con, municipios):
    _base(con, municipios)
    ontem = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.upsert_conta(con, {"username": "portal.com.br", "nome": "Portal do Tocantins", "completo": 1,
                          "origem": "noticias"}, rede="web")  # como o coletor de notícias grava o site
    con.execute("INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, medido_em) VALUES "
                "('web', 'w1', 'portal.com.br', 'https://news.google.com/rss/articles/w1', 'Materia', ?, "
                "'Energisa anuncia obra de rede em Gurupi', 'x')", (ontem,))
    con.execute("INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, medido_em) VALUES "
                "('web', 'w2', 'portal.com.br', 'https://news.google.com/rss/articles/w2', 'Materia', ?, "
                "'Feira de artesanato em Palmas', 'x')", (ontem,))  # matéria que a IA não ligou à Energisa
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, tema, setor, sentimento, alcance, "
                "resumo, analisada_em) VALUES ('web', 'w1', 1, 1, 'obras_investimento', 'obras_expansao', 'positivo', "
                "'local', 'Energisa anuncia obra de rede em Gurupi.', 'x')")
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, analisada_em) "
                "VALUES ('web', 'w2', 0, 0, 'x')")
    noticias = painel.linha(con, "noticias")
    assert [(p["id"], p["rede"], p["nome"]) for p in noticias["itens"]] == [("w1", "web", "Portal do Tocantins")]
    linhas = {l["id"]: l for l in painel.inicio(con)["linhas"]}
    assert "w1" in {p["id"] for p in linhas["mencoes"]["itens"]}
    assert "w1" not in {p["id"] for p in linhas["top10"]["itens"]}  # site não tem curtidas: fica fora do Top 10
    ficha = painel.post(con, "w1")
    assert ficha["contaInfo"]["nome"] == "Portal do Tocantins" and ficha["onde"]["nome"] == "Gurupi"
    assert painel.post(con, "w2") is None
    recentes = painel.linha(con, "recentes", sobrepor={"dias": "3"})
    assert "w1" in {p["id"] for p in recentes["itens"]} and "w2" not in {p["id"] for p in recentes["itens"]}
    assert [p["id"] for p in painel.busca(con, "obra de rede")["posts"]] == ["w1"]
    assert "obras_expansao" in {s["id"] for s in painel.setores(con)["setores"]}

