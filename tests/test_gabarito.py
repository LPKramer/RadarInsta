from radar import gabarito


def resposta(caso, sobre, sentimento="neutro", tema="outro", municipios=()):
    return {"caso": caso, "sobre_energia": sobre, "sentimento": sentimento, "tema": tema,
            "municipios": list(municipios)}


def caso(numero, dificuldade, sobre, sentimento="", tema=None, municipio=""):
    return {"caso": numero, "dificuldade": dificuldade,
            "esperado": {"sobre": sobre, "sentimento": sentimento, "tema": tema, "municipio": municipio}}


def test_criterio_de_acerto():
    # Sobre energia: precisa concordar também no sentimento, que decide o alerta.
    assert gabarito.acertou(caso(1, "fácil", "sim", "negativo")["esperado"], resposta(1, True, "negativo"))
    assert not gabarito.acertou(caso(1, "fácil", "sim", "negativo")["esperado"], resposta(1, True, "neutro"))
    assert not gabarito.acertou(caso(1, "fácil", "sim", "negativo")["esperado"], resposta(1, False))
    # Não é sobre energia: basta a IA dizer que não é.
    assert gabarito.acertou(caso(2, "típico", "não")["esperado"], resposta(2, False))
    assert not gabarito.acertou(caso(2, "típico", "não")["esperado"], resposta(2, True, "neutro"))
    # Sem resposta possível: certo se não vira alerta.
    assert gabarito.acertou(caso(3, "sem resposta possível", "não dá para saber")["esperado"],
                            resposta(3, True, "neutro"))
    assert not gabarito.acertou(caso(3, "sem resposta possível", "não dá para saber")["esperado"],
                                resposta(3, True, "negativo"))
    # Ainda não marcado: não conta.
    assert gabarito.acertou(caso(4, "típico", "")["esperado"], resposta(4, True)) is None


def test_conferir_soma_por_dificuldade_e_o_secundario():
    casos = [caso(1, "fácil", "sim", "negativo", "falta_energia", "Gurupi"),
             caso(2, "típico", "não"),
             caso(3, "difícil", "sim", "neutro", "obras_investimento", "Palmas"),
             caso(4, "típico", "")]
    respostas = [resposta(1, True, "negativo", "falta_energia", ["Gurupi"]),
                 resposta(2, True, "neutro"),
                 resposta(3, True, "neutro", "institucional", ["Porto Nacional"]),
                 resposta(4, False)]
    r = gabarito.conferir(casos, respostas)
    assert (r["certos"], r["marcados"]) == (2, 3)
    assert r["por_dificuldade"]["fácil"] == [1, 1] and r["por_dificuldade"]["típico"] == [0, 1]
    assert r["secundario"] == {"tema": [1, 2], "municipio": [1, 2]}
    assert [l["certo"] for l in r["linhas"]] == [True, False, True, None]


def test_planilha_do_resultado(tmp_path):
    r = gabarito.conferir([caso(1, "fácil", "sim", "negativo")], [resposta(1, True, "negativo")])
    caminho = gabarito.salvar_resultado(r, tmp_path / "resultado.xlsx")
    respostas = [{**resposta(1, True, "negativo"), "setor": "operacao", "alcance": "local", "resumo": "x",
                  "confianca": 0.9, "modelo": "m", "versao": "regras 1", "respondido_em": "2026-11-10"}]
    lidas = gabarito.ler_respostas(gabarito.salvar_respostas(respostas, tmp_path / "respostas.xlsx"))
    assert caminho.exists() and lidas[0]["sobre_energia"] is True and lidas[0]["sentimento"] == "negativo"
