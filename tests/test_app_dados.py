import json

from radar import app_dados, mencoes as mc

from conftest import analise, conta, deepseek_falso, post, resposta_deepseek


def test_mapa_projeta_norte_para_cima(tmp_path):
    quadrado = [[[-48.0, -10.0], [-47.0, -10.0], [-47.0, -11.0], [-48.0, -11.0], [-48.0, -10.0]]]
    ilha = [[[-47.0, -12.0], [-46.0, -12.0], [-46.0, -13.0], [-47.0, -12.0]]]
    malha = {"features": [
        {"properties": {"codarea": "1"}, "geometry": {"type": "Polygon", "coordinates": quadrado}},
        {"properties": {"codarea": "2"}, "geometry": {"type": "MultiPolygon", "coordinates": [quadrado, ilha]}},
    ]}
    arquivo = tmp_path / "malha.json"
    arquivo.write_text(json.dumps(malha), encoding="utf-8")

    geo = app_dados.mapa(arquivo, largura=100)
    assert geo["largura"] == 100 and geo["altura"] > 100  # 3° de latitude contra 2° de longitude: mais alto
    assert geo["paths"][1].startswith("M0.0,0.0L50.0,0.0") and geo["paths"][1].endswith("Z")
    assert geo["paths"][2].count("M") == 2


def test_exportar_liga_mencoes_e_contas_aos_municipios(con, municipios, tmp_path):
    conta(con, municipios, "gurupinoticias", [post(1, curtidas=90, comentarios=10, legenda="Apagão", codigo="G1")])
    conta(con, municipios, "pequena", [], prioridade="C")
    cliente, _ = deepseek_falso(lambda _: resposta_deepseek(analise()))
    mc.analisar(con, cliente, mc.candidatos(con), aviso=lambda _: None)

    caminho, n = app_dados.exportar(con, tmp_path / "radar.json")
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    gurupi = next(m["id"] for m in dados["municipios"] if m["nome"] == "Gurupi")

    assert n == {"mencoes": 1, "contas": 1, "municipios": 139}  # a conta C não é monitorada
    assert all(m["d"].startswith("M") for m in dados["municipios"])  # a malha cobre os 139 códigos do IBGE
    mencao = dados["mencoes"][0]
    assert mencao["id"] == "id-G1" and mencao["municipios"] == [gurupi] and mencao["interacoes"] == 100
    assert mencao["reacao"] is None and mencao["citaEnergisa"] is False
    assert dados["contas"][0]["username"] == "gurupinoticias" and dados["contas"][0]["municipios"] == [gurupi]
