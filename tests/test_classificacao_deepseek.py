import pytest

from radar import classificacao as cl, classificacao_deepseek as ds, perfis

from conftest import classificacao, perfil
from conftest import deepseek_falso as cliente_falso, resposta_deepseek as resposta


def test_parametros_modo_json_sem_raciocinio(municipios):
    conta = perfis.normalizar(perfil("palmasagora"))
    params = ds.parametros(conta, cl.sistema(municipios), "deepseek-flash")
    assert params["response_format"] == {"type": "json_object"}
    assert params["extra_body"] == {"thinking": {"type": "disabled"}}
    sistema = params["messages"][0]["content"]
    assert "JSON" in sistema and "perfil_fofoca" in sistema and "Gurupi" in sistema
    assert "@palmasagora" in params["messages"][1]["content"]


def test_repete_quando_vem_em_branco(municipios):
    respostas = iter([resposta("   "), resposta(classificacao())])
    cliente, chamadas = cliente_falso(lambda _: next(respostas))
    dados, modelo = ds.classificar_uma(cliente, perfis.normalizar(perfil("x")), "sistema")
    assert dados["tipo"] == "portal_noticias" and modelo == "deepseek-flash" and len(chamadas) == 2

    cliente, _ = cliente_falso(lambda _: resposta(""))
    with pytest.raises(cl.ClassificacaoFalhou):
        ds.classificar_uma(cliente, perfis.normalizar(perfil("x")), "sistema")
    cliente, _ = cliente_falso(lambda _: resposta(classificacao(), finish_reason="length"))
    with pytest.raises(cl.ClassificacaoFalhou):
        ds.classificar_uma(cliente, perfis.normalizar(perfil("x")), "sistema")


def test_validar_normaliza_respostas_fora_do_formato():
    dados = cl.validar({"tipo": "perfil_fofoca", "publica_noticias_locais": "true", "municipios": "Palmas",
                        "abrangencia": "cidade", "confianca": "0.8"})
    assert dados == {"tipo": "perfil_fofoca", "publica_noticias_locais": True, "municipios": ["Palmas"],
                     "abrangencia": "indefinida", "confianca": 0.8, "justificativa": ""}
    with pytest.raises(cl.ClassificacaoFalhou):
        cl.validar({"tipo": "noticias"})
    with pytest.raises(cl.ClassificacaoFalhou):
        cl.validar(["portal_noticias"])


def test_classificar_em_paralelo_grava_e_conta_falhas(con, municipios):
    for u in ("portal", "quebrado", "invalido"):
        perfis.ingerir(con, perfil(u), "busca", municipios)

    def responder(params):
        texto = params["messages"][1]["content"]
        if "@quebrado" in texto:
            return resposta("{nao é json")
        if "@invalido" in texto:
            return resposta(classificacao(tipo="outro"))
        return resposta(classificacao())

    cliente, _ = cliente_falso(responder)
    ok, falhas = ds.classificar(con, cliente, cl.pendentes(con), paralelo=3, aviso=lambda _: None)
    assert (ok, falhas) == (1, 2)
    linha = con.execute("SELECT * FROM classificacoes").fetchone()
    assert linha["username"] == "portal" and linha["modelo"] == "deepseek-flash"


def test_saldo_esgotado_interrompe(con, municipios):
    import httpx2 as httpx
    import openai

    perfis.ingerir(con, perfil("a"), "busca", municipios)
    requisicao = httpx.Request("POST", "https://api.deepseek.com/chat/completions")

    def responder(_):
        raise openai.APIStatusError("Insufficient Balance", response=httpx.Response(402, request=requisicao),
                                    body=None)

    cliente, _ = cliente_falso(responder)
    with pytest.raises(ds.SaldoInsuficiente):
        ds.classificar(con, cliente, cl.pendentes(con), aviso=lambda _: None)


def erro_de_conexao(causa: BaseException):
    import httpx2
    import openai

    erro = openai.APIConnectionError(request=httpx2.Request("POST", "https://api.deepseek.com/chat/completions"))
    erro.__cause__ = causa
    return erro


def test_falha_de_conexao_diz_o_motivo():
    import socket
    import ssl

    certificado = ds.causa_da_conexao(erro_de_conexao(ssl.SSLCertVerificationError("certificate verify failed")))
    assert "SSLCertVerificationError" in certificado and "antivírus" in certificado
    dns = ds.causa_da_conexao(erro_de_conexao(socket.gaierror(11001, "getaddrinfo failed")))
    assert "não foi encontrado" in dns
    assert "não respondeu" in ds.causa_da_conexao(erro_de_conexao(TimeoutError("timed out")))


def test_sem_conexao_para_depois_de_falhas_seguidas():
    import socket

    def falha():
        raise erro_de_conexao(socket.gaierror(11001, "getaddrinfo failed"))

    avisos = []
    tarefas = {f"conta{i}": falha for i in range(40)}
    with pytest.raises(ds.SemConexao, match="nada foi cobrado"):
        ds.em_paralelo(tarefas, lambda *_: None, paralelo=1, aviso=avisos.append)
    assert len(avisos) == ds.SEM_CONEXAO_SEGUIDAS - 1  # não tenta as 40


def test_cliente_usa_os_certificados_do_windows(monkeypatch):
    import truststore

    from radar import config

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    assert isinstance(ds.contexto_tls(), truststore.SSLContext)
    assert ds.criar_cliente() is not None
