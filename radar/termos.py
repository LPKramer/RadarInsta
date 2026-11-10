"""Geração dos termos de busca por município, com mais termos para cidades maiores.

Os termos vão sem acento: o ator de busca da Apify não aceita caracteres especiais.
"""
from dataclasses import dataclass

from .municipios import compactar, nome_curto, normalizar

# {c} = nome da cidade sem acento; {cc} = nome colado (ex.: 'portonacional').
MODELOS_POR_PORTE = {
    "T1": ["{c}", "{c} noticias", "noticias {c}", "{c} news", "fofoca {c}", "{c} agora",
           "plantao {c}", "acontece {c}", "{c} urgente", "{c} online", "{cc}to", "{c} em foco"],
    "T2": ["{c}", "{c} noticias", "noticias {c}", "fofoca {c}", "plantao {c}", "{cc}to"],
    "T3": ["{c}", "{c} noticias", "{cc}to"],
}

TERMOS_ESTADO = [
    "tocantins noticias", "noticias tocantins", "fofoca tocantins", "tocantins news",
    "plantao tocantins", "tocantins urgente", "bico do papagaio", "jalapao",
    "norte do tocantins", "sul do tocantins", "sudeste do tocantins",
]

# Hashtags para a fonte opcional de mineração de autores.
HASHTAGS_ESTADO = ["tocantins", "noticiastocantins", "palmasto", "araguaina", "gurupi",
                   "portonacional", "bicodopapagaio", "jalapao"]


@dataclass(frozen=True)
class Termo:
    termo: str
    ibge_id: int | None  # None para termos estaduais/regionais


def termos_do_municipio(m: dict) -> list[str]:
    c = normalizar(nome_curto(m["nome"]))
    cc = compactar(nome_curto(m["nome"]))
    return [modelo.format(c=c, cc=cc) for modelo in MODELOS_POR_PORTE[m["porte"]]]


def gerar(municipios, incluir_estado: bool = True) -> list[Termo]:
    """Lista sem repetição. Se dois municípios gerarem o mesmo termo, fica o primeiro (o maior)."""
    vistos: dict[str, Termo] = {}
    for m in municipios:
        for t in termos_do_municipio(m):
            vistos.setdefault(t, Termo(t, m["ibge_id"]))
    if incluir_estado:
        for t in TERMOS_ESTADO:
            vistos.setdefault(t, Termo(t, None))
    return list(vistos.values())


def hashtags(municipios) -> list[str]:
    """Hashtags estaduais + uma por município de porte T1/T2 (ex.: #colinasdotocantins)."""
    tags = list(HASHTAGS_ESTADO)
    for m in municipios:
        if m["porte"] in ("T1", "T2"):
            tags.append(compactar(m["nome"]))
    return list(dict.fromkeys(tags))


# Vai o nome oficial, que desfaz a ambiguidade dos "... do Tocantins". Na primeira consulta, exigir a palavra
# "tocantins" deixava de fora páginas como @portalcamposlindos; as de outros estados que aparecem a mais a IA descarta.
# A segunda só roda para o município que continuar descoberto: sem palavras de notícia, mas presa ao estado.
MODELOS_GOOGLE = [
    'site:instagram.com "{nome}" (noticias OR fofoca OR plantao OR news OR portal OR acontece)',
    'site:instagram.com "{nome}" tocantins',
]


def consultas_google(municipios) -> list[Termo]:
    """Consultas `site:instagram.com` para achar páginas que a busca interna do Instagram não mostra."""
    return [Termo(modelo.format(nome=m["nome"]), m["ibge_id"]) for m in municipios for modelo in MODELOS_GOOGLE]


# Portais estaduais e regionais nem sempre levam o nome de um município. Valem a pena com várias páginas de
# resultado, assim como as cidades grandes (T1), que têm mais páginas do que a primeira página do Google mostra.
CONSULTAS_GOOGLE_ESTADO = [
    "site:instagram.com tocantins (noticias OR jornal OR jornalismo OR portal)",
    "site:instagram.com tocantins (plantao OR policial OR urgente OR alerta)",
    'site:instagram.com tocantins (fofoca OR acontece OR babado OR "fica sabendo")',
    "site:instagram.com tocantins (radio OR fm OR podcast OR tv)",
    'site:instagram.com "bico do papagaio" (noticias OR portal OR acontece OR plantao)',
    "site:instagram.com jalapao (noticias OR portal OR acontece)",
    'site:instagram.com "norte do tocantins" (noticias OR portal OR acontece)',
    'site:instagram.com "sul do tocantins" (noticias OR portal OR acontece)',
    'site:instagram.com "sudeste do tocantins" (noticias OR portal OR acontece)',
    'site:instagram.com "vale do araguaia" tocantins (noticias OR portal OR acontece)',
]
MODELO_GOOGLE_CIDADE_GRANDE = ('site:instagram.com "{nome}" tocantins '
                               '(jornal OR plantao OR policial OR radio OR fofoca OR urgente)')


def consultas_google_estado(municipios) -> list[Termo]:
    return [Termo(c, None) for c in CONSULTAS_GOOGLE_ESTADO] + [
        Termo(MODELO_GOOGLE_CIDADE_GRANDE.format(nome=m["nome"]), m["ibge_id"])
        for m in municipios if m["porte"] == "T1"
    ]
