"""Etapa 3: pré-filtro por regras e coleta do perfil completo das contas que só têm o @."""
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import config, municipios as mun, perfis
from .apify import tamanho_lote
from .descoberta import lotes

# Sinais de que o perfil publica notícia. Casam no início de palavra ("noticia" pega "noticias").
RE_NOTICIA = re.compile(r"\b(noticia|jornal|portal|informa|fofoc|babado|plantao|urgente|news|acontece|"
                        r"radio|tv|reporter|jornalis|comunicador|blog|policia|midia|media)")
NOTICIA_NO_USERNAME = ("noticia", "news", "fofoca", "plantao", "urgente", "portal", "jornal", "informa",
                       "acontece", "policia", "blog", "radio", "agora", "online", "emfoco")
RE_COMERCIO = re.compile(r"\b(delivery|pedido|encomenda|loja|moda|boutique|salao|estetica|imoveis|imobiliaria|"
                         r"vendas|atacado|varejo|cardapio|agende|agendamento|orcamento|clinica|odonto|"
                         r"unhas|cilios|sobrancelha|barbearia|restaurante|hamburgueria|pizzaria|acai|"
                         r"confeitaria|cosmetico|semijoia|calcado|roupas|frete|parcelamos|promocao|cupom|"
                         r"store|shop|restaurant|salon|beauty|clothing|dentist|real estate|bakery|gym)")


# A descoberta das mais seguidas (influentes.py) tem triagem própria. Influenciadores de moda e beleza cairiam no
# filtro de comércio abaixo ("moda", "beauty", "cupom"), então ele não vale para as contas que vêm dela.
ORIGEM_INFLUENTES = "influentes"


def motivo_descarte(conta: dict, agora: datetime | None = None) -> str | None:
    """Motivo para não gastar enriquecimento/IA com a conta, ou None se ela segue no funil."""
    if conta.get("origem") == "manual":
        return None
    if conta.get("privada"):
        return "privada"
    if not conta.get("completo"):
        return None
    if (conta.get("seguidores") or 0) < config.SEGUIDORES_MIN:
        return "poucos_seguidores"

    agora = agora or datetime.now(timezone.utc)
    ultimo = perfis.ler_data(conta.get("ultimo_post_em"))
    if conta.get("n_posts") == 0 or (ultimo and ultimo < agora - timedelta(days=config.DIAS_INATIVA)):
        return "inativa"

    texto = mun.normalizar(f"{conta.get('nome') or ''} {conta.get('bio') or ''} {conta.get('categoria') or ''}")
    username = conta.get("username") or ""
    tem_noticia = bool(RE_NOTICIA.search(texto)) or any(p in username for p in NOTICIA_NO_USERNAME)
    if RE_COMERCIO.search(texto) and not tem_noticia and conta.get("origem") != ORIGEM_INFLUENTES:
        return "comercial"
    return None


def prefiltrar(con) -> Counter:
    """Aplica as regras às contas não descartadas e ainda sem classificação da IA.

    Devolve quantas caíram em cada motivo.
    """
    motivos = Counter()
    linhas = con.execute(
        "SELECT * FROM contas c WHERE rede = 'instagram' AND descartada IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM classificacoes k WHERE k.rede = c.rede AND k.username = c.username)"
    ).fetchall()
    for linha in linhas:
        motivo = motivo_descarte(dict(linha))
        if motivo:
            con.execute("UPDATE contas SET descartada = ? WHERE rede = ? AND username = ?",
                        (motivo, linha["rede"], linha["username"]))
            motivos[motivo] += 1
    con.commit()
    return motivos


def pendentes(con, limite: int | None = None) -> list[dict]:
    """Contas que só têm o @ (vindas de relacionadas, hashtags, Google ou manual)."""
    sql = ("SELECT username, origem, profundidade FROM contas WHERE rede = 'instagram' AND completo = 0 "
           "AND descartada IS NULL "
           "ORDER BY profundidade, descoberta_em")
    if limite:
        sql += f" LIMIT {int(limite)}"
    return [dict(l) for l in con.execute(sql)]


def custo(n_perfis: int) -> float:
    return n_perfis * config.PRECO_PERFIL_1K / 1000


def enriquecer(con, apify, limite: int | None = None, aviso=print) -> dict:
    municipios = mun.carregar(con)
    fila = pendentes(con, limite)
    resumo = Counter()
    for i, lote in enumerate(lotes(fila, tamanho_lote(apify, config.PERFIS_POR_RUN)), 1):
        aviso(f"Perfis: lote {i} ({len(lote)} contas)...")
        por_username = {c["username"]: c for c in lote}
        _, itens = apify.rodar(config.ATOR_PERFIL, {"usernames": list(por_username)}, "perfil")
        vistos = set()
        for item in itens:
            conta = por_username.get((item.get("username") or "").lower())
            if conta and perfis.ingerir(con, item, conta["origem"], municipios, conta["profundidade"]):
                vistos.add(conta["username"])
        for username in set(por_username) - vistos:
            con.execute("UPDATE contas SET descartada = 'nao_encontrada' WHERE rede = 'instagram' "
                        "AND username = ?", (username,))
        con.commit()
        resumo["enriquecidas"] += len(vistos)
        resumo["nao_encontradas"] += len(por_username) - len(vistos)
    resumo.update({f"descartada_{k}": v for k, v in prefiltrar(con).items()})
    return dict(resumo)
