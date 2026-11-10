"""Matérias de sites de notícia sobre energia e a Energisa no Tocantins, pelo RSS de busca do Google Notícias.

Grátis e sem conta: cada consulta devolve até 100 matérias (título, veículo, data e link). Elas entram na mesma base
das menções do Instagram, com rede = 'web': o veículo vira a "conta" (ex.: afnoticias.com.br, "AF Notícias") e a
matéria vira o "post", com o título como legenda. Só ficam as matérias cujo título cita o Tocantins ou um município
do TO; a IA analisa as que passam no filtro de palavras-chave, como no Instagram.
"""
from __future__ import annotations

import random
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import unescape

from . import db, mencoes, municipios as mun

REDE = "web"
ORIGEM = "google_noticias"
TIPO = "Materia"
URL = "https://news.google.com/rss/search"
AGENTE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
PAUSA = (2.0, 4.0)

CONSULTAS_MARCA = ['"Energisa Tocantins"', "Energisa Tocantins", "Energisa TO", "Celtins Tocantins"]
CONSULTAS_TEMA = [
    '"falta de energia" Tocantins', '"queda de energia" Tocantins', '"sem energia" Tocantins', "apagão Tocantins",
    '"conta de luz" Tocantins', '"tarifa de energia" Tocantins', '"desligamento programado" Tocantins',
    '"rede elétrica" Tocantins', '"choque elétrico" Tocantins', '"interrupção de energia" Tocantins',
    '"fornecimento de energia" Tocantins', "poste energia Tocantins",
]
# Nas cidades maiores, também a marca com o nome da cidade; nas maiores ainda, a falta de energia sem a marca.
MODELO_MARCA_CIDADE = 'Energisa "{nome}"'
# "Tocantins" junto: há Palmas, Alvorada e Paraíso em outros estados (o rbj.com.br, de Palmas-PR, entrou assim).
MODELOS_TEMA_CIDADE = ['"falta de energia" "{nome}" Tocantins', '"sem energia" "{nome}" Tocantins']
# Matéria que cita outra distribuidora é de outro estado.
RE_OUTRA_DISTRIBUIDORA = re.compile(r"\b(copel|equatorial|cemig|enel|neoenergia|coelba|celpe|cosern|cemar|celpa|"
                                    r"ceee|rge|light|cpfl|elektro|amazonas energia|roraima energia|celesc|"
                                    r"energisa (mt|ms|mato grosso|sul|sergipe|paraiba|rondonia|acre|minas))\b")


def consultas(municipios) -> list[str]:
    lista = CONSULTAS_MARCA + CONSULTAS_TEMA
    for m in municipios:
        porte = mun.porte(m["populacao"])
        if porte in ("T1", "T2"):
            lista.append(MODELO_MARCA_CIDADE.format(nome=m["nome"]))
        if porte == "T1":
            lista += [modelo.format(nome=m["nome"]) for modelo in MODELOS_TEMA_CIDADE]
    return list(dict.fromkeys(lista))


def endereco(consulta: str, dias: int | None) -> str:
    q = f"{consulta} when:{dias}d" if dias else consulta
    return f"{URL}?{urllib.parse.urlencode({'q': q, 'hl': 'pt-BR', 'gl': 'BR', 'ceid': 'BR:pt-419'})}"


def _contexto_tls():
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def baixar(url: str, tentativas: int = 3, dormir=time.sleep) -> bytes:
    """O RSS da consulta. Em 429/503 (excesso de pedidos), espera e tenta de novo."""
    pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept-Language": "pt-BR,pt;q=0.9"})
    for tentativa in range(1, tentativas + 1):
        try:
            with urllib.request.urlopen(pedido, timeout=30, context=_contexto_tls()) as resposta:
                return resposta.read()
        except urllib.error.HTTPError as erro:
            if erro.code not in (429, 500, 502, 503) or tentativa == tentativas:
                raise
        except urllib.error.URLError:
            if tentativa == tentativas:
                raise
        dormir(30 * tentativa)
    raise RuntimeError("inalcançável")


def _dominio(url: str | None) -> str | None:
    host = urllib.parse.urlparse(url or "").hostname or ""
    return host.removeprefix("www.") or None


def ler_rss(conteudo: bytes) -> list[dict]:
    """Itens do RSS do Google Notícias -> {id, titulo, veiculo, dominio, site, link, publicado_em}."""
    itens = []
    for item in ET.fromstring(conteudo).iter("item"):
        fonte = item.find("source")
        veiculo = (fonte.text or "").strip() if fonte is not None else ""
        site = fonte.get("url") if fonte is not None else None
        titulo = unescape((item.findtext("title") or "").strip())
        if veiculo and titulo.endswith(f" - {veiculo}"):
            titulo = titulo[: -len(veiculo) - 3].strip()
        try:
            publicado = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(timezone.utc)
            publicado_em = publicado.isoformat(timespec="seconds").replace("+00:00", "Z")
        except (TypeError, ValueError):
            publicado_em = None
        identificador = (item.findtext("guid") or item.findtext("link") or "").strip()
        dominio = _dominio(site)
        if identificador and titulo and dominio:
            itens.append({"id": identificador, "titulo": titulo, "veiculo": veiculo or dominio, "dominio": dominio,
                          "site": site, "link": (item.findtext("link") or "").strip(), "publicado_em": publicado_em})
    return itens


def do_tocantins(titulo: str, municipios) -> bool:
    texto = mun.normalizar(titulo)
    if RE_OUTRA_DISTRIBUIDORA.search(texto):
        return False
    return " tocantins" in f" {texto}" or bool(mun.no_texto(titulo, municipios))


def veiculos_fora(con) -> set[str]:
    """Veículos marcados como de fora do TO (descartada na tabela contas): as matérias deles não entram."""
    return {l[0] for l in con.execute("SELECT username FROM contas WHERE rede = ? AND descartada IS NOT NULL", (REDE,))}


def ingerir(con, itens: list[dict], municipios) -> Counter:
    """Grava veículos e matérias do TO. Uma matéria já gravada (de outra consulta) não conta de novo."""
    resumo = Counter()
    fora = veiculos_fora(con)
    for item in itens:
        if item["dominio"] in fora or not do_tocantins(item["titulo"], municipios):
            resumo["fora_do_to"] += 1
            continue
        db.upsert_conta(con, {"username": item["dominio"], "nome": item["veiculo"], "url_externa": item["site"],
                              "completo": 1, "origem": ORIGEM}, rede=REDE)
        cursor = con.execute(
            """INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, medido_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (rede, post_id) DO NOTHING""",
            (REDE, item["id"], item["dominio"], item["link"], TIPO, item["publicado_em"], item["titulo"], db.agora()))
        resumo["novas" if cursor.rowcount else "repetidas"] += 1
    return resumo


def coletar(con, dias: int | None = 90, aviso=print, baixar_=baixar, dormir=time.sleep) -> dict:
    """Roda todas as consultas e grava as matérias novas. Uma consulta que falhar não para as outras."""
    municipios = mun.carregar(con)
    resumo = Counter()
    lista = consultas(municipios)
    for i, consulta in enumerate(lista):
        if i:
            dormir(random.uniform(*PAUSA))
        try:
            itens = ler_rss(baixar_(endereco(consulta, dias)))
        except (urllib.error.URLError, ET.ParseError, OSError) as erro:
            aviso(f"Consulta {consulta!r} falhou ({type(erro).__name__}: {erro}); sigo com as outras.")
            resumo["consultas_com_erro"] += 1
            continue
        resumo.update(ingerir(con, itens, municipios))
        con.commit()
        resumo["consultas"] += 1
        if (i + 1) % 10 == 0:
            aviso(f"{i + 1} de {len(lista)} consultas; {resumo['novas']} matérias novas do TO até agora.")
    return dict(resumo)


SQL_CANDIDATOS = """
SELECT p.*, c.nome AS veiculo, 'site_noticias' AS tipo_conta, NULL AS municipios_conta
FROM posts p JOIN contas c ON c.rede = p.rede AND c.username = p.username
WHERE p.rede = 'web' AND NOT EXISTS (SELECT 1 FROM mencoes x WHERE x.rede = p.rede AND x.post_id = p.post_id)
ORDER BY p.publicado_em DESC
"""


def candidatos(con) -> list[dict]:
    """Matérias ainda não analisadas que passam no filtro de palavras-chave."""
    return [dict(l) for l in con.execute(SQL_CANDIDATOS) if mencoes.menciona_energia(l["legenda"])]


def alertas(con, posts: list[dict], aviso=print) -> int:
    """Matéria negativa vira alerta, no mesmo arquivo da vigilância."""
    from .vigia import alertar

    return alertar(con, posts, aviso)


def situacao(con) -> dict:
    linha = con.execute(
        """SELECT COUNT(*), COUNT(DISTINCT p.username), SUM(x.sobre_energia), SUM(x.sentimento = 'negativo'
                  AND x.sobre_energia = 1)
           FROM posts p LEFT JOIN mencoes x ON x.rede = p.rede AND x.post_id = p.post_id WHERE p.rede = 'web'"""
    ).fetchone()
    return {"materias": linha[0], "veiculos": linha[1], "sobre_energia": linha[2] or 0, "negativas": linha[3] or 0,
            "a_analisar": len(candidatos(con)), "consultas": len(consultas(mun.carregar(con)))}
