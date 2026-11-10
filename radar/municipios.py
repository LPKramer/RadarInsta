"""Municípios do Tocantins (IBGE), porte por população e casamento de nomes em textos."""
import csv
import json
import re

import requests
from rapidfuzz import fuzz, process
from unidecode import unidecode

from . import config

URL_MUNICIPIOS = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/17/municipios"
# Censo 2022, tabela 4709 (população residente), todos os municípios do TO.
URL_POPULACAO = "https://apisidra.ibge.gov.br/values/t/4709/n6/in%20n3%2017/v/93/p/2022"

# Apelidos usados em perfis locais. Acrescente aqui conforme a equipe identificar.
APELIDOS = {
    "Palmas": ["pmw"],
}

SUFIXO_ESTADO = re.compile(r"\s+do\s+tocantins$", re.IGNORECASE)
COLUNAS_CSV = ["ibge_id", "nome", "microrregiao", "regiao_imediata", "populacao"]


def normalizar(texto: str | None) -> str:
    """Minúsculo, sem acento e com espaços simples."""
    texto = unidecode(texto or "").lower()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return texto.strip()


def nome_curto(nome: str) -> str:
    """'Paraíso do Tocantins' -> 'Paraíso': é como as páginas locais costumam chamar a cidade."""
    return SUFIXO_ESTADO.sub("", nome).strip()


def compactar(texto: str) -> str:
    return normalizar(texto).replace(" ", "")


def variantes(nome: str) -> list[str]:
    formas = {normalizar(nome), normalizar(nome_curto(nome))}
    formas |= {compactar(nome), compactar(nome_curto(nome)), compactar(nome_curto(nome)) + "to"}
    formas |= set(APELIDOS.get(nome, []))
    return sorted(formas)


def porte(populacao: int | None) -> str:
    if populacao is None:
        return "T3"
    if populacao > 40_000:
        return "T1"
    if populacao >= 10_000:
        return "T2"
    return "T3"


def baixar_ibge(timeout: int = 60) -> list[dict]:
    """Baixa municípios e população do IBGE. Grava o CSV de reserva para uso offline."""
    municipios = requests.get(URL_MUNICIPIOS, timeout=timeout).json()
    linhas_pop = requests.get(URL_POPULACAO, timeout=timeout).json()[1:]  # a 1ª linha é o cabeçalho
    populacao = {int(l["D1C"]): int(l["V"]) for l in linhas_pop if l["V"].isdigit()}

    registros = []
    for m in municipios:
        imediata = m.get("regiao-imediata") or {}
        micro = m.get("microrregiao") or {}
        registros.append({
            "ibge_id": int(m["id"]),
            "nome": m["nome"],
            "microrregiao": micro.get("nome"),
            "regiao_imediata": imediata.get("nome"),
            "populacao": populacao.get(int(m["id"])),
        })

    config.MUNICIPIOS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(config.MUNICIPIOS_CSV, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUNAS_CSV)
        escritor.writeheader()
        escritor.writerows(registros)
    return registros


def ler_csv() -> list[dict]:
    with open(config.MUNICIPIOS_CSV, encoding="utf-8") as f:
        return [
            {**l, "ibge_id": int(l["ibge_id"]), "populacao": int(l["populacao"]) if l["populacao"] else None}
            for l in csv.DictReader(f)
        ]


def salvar(con, registros: list[dict]) -> int:
    for r in registros:
        con.execute(
            "INSERT OR REPLACE INTO municipios (ibge_id, nome, nome_norm, nome_curto, variantes, "
            "microrregiao, regiao_imediata, populacao, porte) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (r["ibge_id"], r["nome"], normalizar(r["nome"]), nome_curto(r["nome"]),
             json.dumps(variantes(r["nome"])), r["microrregiao"], r["regiao_imediata"],
             r["populacao"], porte(r["populacao"])),
        )
    con.commit()
    return len(registros)


def carregar(con) -> list[dict]:
    """Municípios do banco, do maior para o menor."""
    linhas = con.execute("SELECT * FROM municipios ORDER BY populacao DESC").fetchall()
    return [{**dict(l), "variantes": json.loads(l["variantes"])} for l in linhas]


def no_texto(texto: str, municipios) -> set[int]:
    """Municípios citados como palavra inteira num texto livre (bio, nome do perfil)."""
    alvo = f" {normalizar(texto)} "
    return {m["ibge_id"] for m in municipios if any(f" {v} " in alvo for v in m["variantes"])}


def no_username(username: str, municipios) -> set[int]:
    """Municípios embutidos no @ (ex.: 'gurupinoticias'). Só formas com 6+ letras, para evitar ruído."""
    alvo = compactar(username)
    return {
        m["ibge_id"] for m in municipios
        if any(len(v) >= 6 and " " not in v and v in alvo for v in m["variantes"])
    }


def casar_nome(nome: str, municipios, corte: int = 88) -> int | None:
    """Casa um nome de município escrito livremente (ex.: saída da IA) com o código do IBGE."""
    escolhas = {}
    for m in municipios:
        escolhas[m["nome_norm"]] = m["ibge_id"]
        escolhas.setdefault(normalizar(m["nome_curto"]), m["ibge_id"])
    alvo = re.sub(r"( do)? (to|tocantins)$", "", normalizar(nome))
    if not alvo:
        return None
    if alvo in escolhas:
        return escolhas[alvo]
    melhor = process.extractOne(alvo, list(escolhas), scorer=fuzz.ratio, score_cutoff=corte)
    return escolhas[melhor[0]] if melhor else None
