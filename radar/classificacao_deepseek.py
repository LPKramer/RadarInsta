"""Classificação com a DeepSeek (API compatível com OpenAI, modo JSON, sem raciocínio).

A DeepSeek não tem batch nem schema estrito: as requisições vão em paralelo e a resposta passa por
`classificacao.validar`. O cache de contexto da DeepSeek é automático, então o sistema (instruções +
municípios), igual em todas as requisições, sai quase de graça a partir da segunda conta.
"""
import json
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial

import openai
from openai import OpenAI

from . import config, municipios as mun
from .classificacao import ABRANGENCIAS, TIPOS, ClassificacaoFalhou, descrever, salvar, sistema, validar

URL_BASE = "https://api.deepseek.com"
# No modo JSON a API às vezes devolve conteúdo em branco; a documentação recomenda repetir.
REPETICOES_EM_BRANCO = 2
# Estimativa conservadora (preço de pico do deepseek-flash): ~700 tokens novos de entrada, ~1500 em cache
# e ~200 de saída por conta.
CUSTO_POR_CONTA = 0.0005

FORMATO = f"""

Responda somente com um objeto JSON, sem texto fora dele, neste formato:
{{"tipo": "portal_noticias", "publica_noticias_locais": true, "municipios": ["Gurupi"], \
"abrangencia": "municipal", "confianca": 0.9, "justificativa": "Publica notícias diárias de Gurupi."}}
Valores aceitos em "tipo": {", ".join(TIPOS)}.
Valores aceitos em "abrangencia": {", ".join(ABRANGENCIAS)}."""


# Falhas de conexão seguidas que encerram a rodada: se a rede não alcança a DeepSeek, não adianta tentar as outras.
SEM_CONEXAO_SEGUIDAS = 3


class SaldoInsuficiente(RuntimeError):
    pass


class SemConexao(RuntimeError):
    pass


def contexto_tls() -> ssl.SSLContext:
    """Confia nos certificados do Windows (os mesmos do Edge), não só na lista do certifi. Antivírus e redes que
    inspecionam HTTPS instalam um certificado próprio no Windows; sem isto, toda chamada dá "Connection error."."""
    import truststore

    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def criar_cliente() -> OpenAI:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY não configurada. Copie .env.example para .env e preencha.")
    return OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=URL_BASE, timeout=120, max_retries=3,
                  http_client=openai.DefaultHttpxClient(verify=contexto_tls()))


def causa_da_conexao(erro: BaseException) -> str:
    """O motivo real por trás do "Connection error." do SDK, com uma dica do que fazer."""
    cadeia, atual = [], erro.__cause__ or erro.__context__
    while atual is not None and len(cadeia) < 8:
        cadeia.append(atual)
        atual = atual.__cause__ or atual.__context__
    texto = " / ".join(dict.fromkeys(f"{type(e).__name__}: {e}" for e in cadeia if str(e))) or str(erro)
    minusculo = texto.lower()
    if any(isinstance(e, ssl.SSLError) for e in cadeia) or "certificate" in minusculo:
        dica = ("o certificado HTTPS da DeepSeek não foi aceito: um antivírus ou a rede está inspecionando o HTTPS. "
                "Teste em outra rede (ex.: o 4G do celular) ou libere api.deepseek.com no antivírus.")
    elif any(isinstance(e, socket.gaierror) for e in cadeia) or "getaddrinfo" in minusculo or "11001" in minusculo:
        dica = ("o nome api.deepseek.com não foi encontrado: sem internet ou a rede (DNS) bloqueia o endereço. "
                "Teste em outra rede (ex.: o 4G do celular).")
    elif "timeout" in minusculo or "timed out" in minusculo:
        dica = ("a DeepSeek não respondeu a tempo: firewall ou rede bloqueando api.deepseek.com. "
                "Teste em outra rede (ex.: o 4G do celular).")
    elif "refused" in minusculo or "10061" in minusculo or "10013" in minusculo:
        dica = "a conexão foi recusada: firewall ou antivírus bloqueando o Python."
    elif "proxy" in minusculo:
        dica = "erro no proxy configurado (variáveis HTTP_PROXY/HTTPS_PROXY)."
    else:
        dica = ""
    return texto + (f"\n  -> {dica}" if dica else "")


def testar(cliente: OpenAI) -> list[str]:
    """Uma chamada grátis (lista de modelos) para conferir rede e chave. Devolve os modelos disponíveis."""
    try:
        return [m.id for m in cliente.models.list()]
    except openai.APIConnectionError as e:
        raise SemConexao(f"Sem conexão com a DeepSeek: {causa_da_conexao(e)}") from e


def custo(n_contas: int) -> float:
    return n_contas * CUSTO_POR_CONTA


def parametros_json(sistema_completo: str, usuario: str, modelo: str, max_tokens: int = 800) -> dict:
    return {
        "model": modelo,
        "messages": [{"role": "system", "content": sistema_completo}, {"role": "user", "content": usuario}],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "temperature": 0,
        # O deepseek-flash raciocina por padrão; para classificar não vale o custo.
        "extra_body": {"thinking": {"type": "disabled"}},
    }


def parametros(conta: dict, texto_sistema: str, modelo: str = config.MODELO) -> dict:
    return parametros_json(texto_sistema + FORMATO, descrever(conta), modelo)


def pedir_json(cliente: OpenAI, params: dict) -> tuple[object, str]:
    """Faz a chamada em modo JSON. Devolve (JSON decodificado, modelo que respondeu)."""
    for _ in range(REPETICOES_EM_BRANCO + 1):
        resposta = cliente.chat.completions.create(**params)
        escolha = resposta.choices[0]
        if escolha.finish_reason == "length":
            raise ClassificacaoFalhou("resposta cortada por max_tokens")
        texto = (escolha.message.content or "").strip()
        if texto:
            return json.loads(texto), resposta.model
    raise ClassificacaoFalhou("resposta em branco")


def classificar_uma(cliente: OpenAI, conta: dict, texto_sistema: str,
                    modelo: str = config.MODELO) -> tuple[dict, str]:
    """Devolve (classificação validada, modelo que respondeu)."""
    dados, modelo_resposta = pedir_json(cliente, parametros(conta, texto_sistema, modelo))
    return validar(dados), modelo_resposta


def em_paralelo(tarefas: dict, gravar, paralelo: int = 8, aviso=print, nome: str = "prontas") -> tuple[int, int]:
    """Roda `tarefas` ({rótulo: função sem argumentos}) em paralelo; `gravar(rótulo, resultado)` fica na thread
    principal, por causa do SQLite. Devolve (ok, falhas)."""
    ok = falhas = sem_conexao = 0
    with ThreadPoolExecutor(max_workers=paralelo) as pool:
        futuros = {pool.submit(funcao): rotulo for rotulo, funcao in tarefas.items()}
        for futuro in as_completed(futuros):
            rotulo = futuros[futuro]
            try:
                resultado = futuro.result()
                sem_conexao = 0
            except (ClassificacaoFalhou, json.JSONDecodeError) as e:
                aviso(f"{rotulo}: {e}")
                falhas += 1
                continue
            except (openai.AuthenticationError, openai.PermissionDeniedError):
                pool.shutdown(cancel_futures=True)
                raise  # credencial errada vale para todas as requisições
            except openai.APIStatusError as e:
                if e.status_code == 402:
                    pool.shutdown(cancel_futures=True)
                    raise SaldoInsuficiente("Saldo da DeepSeek esgotado.") from e
                aviso(f"{rotulo}: erro da API ({e.status_code}) {e.message}")
                falhas += 1
                continue
            except openai.APIConnectionError as e:
                falhas += 1
                sem_conexao += 1
                if sem_conexao >= SEM_CONEXAO_SEGUIDAS:
                    pool.shutdown(cancel_futures=True)
                    raise SemConexao(f"Sem conexão com a DeepSeek ({sem_conexao} falhas seguidas; nada foi cobrado): "
                                     f"{causa_da_conexao(e)}\nTeste com: python -m radar testar-ia") from e
                aviso(f"{rotulo}: falha de conexão ({causa_da_conexao(e)})")
                continue
            gravar(rotulo, resultado)
            ok += 1
            if ok % 25 == 0:
                aviso(f"{ok} de {len(tarefas)} {nome}...")
    return ok, falhas


def classificar(con, cliente: OpenAI, contas: list[dict], modelo: str = config.MODELO, paralelo: int = 8,
                aviso=print) -> tuple[int, int]:
    """Classifica em paralelo e grava cada conta assim que a resposta chega."""
    municipios = mun.carregar(con)
    texto_sistema = sistema(municipios)

    def gravar(username, resultado):
        dados, modelo_resposta = resultado
        salvar(con, username, dados, municipios, modelo_resposta)
        con.commit()

    tarefas = {c["username"]: partial(classificar_uma, cliente, c, texto_sistema, modelo) for c in contas}
    return em_paralelo(tarefas, gravar, paralelo, aviso, "classificadas")
