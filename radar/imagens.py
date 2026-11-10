"""Fotos de perfil e capas das menções que faltam no painel, buscadas pela Apify (pago por resultado).

O painel baixa as imagens enquanto o endereço guardado vale (~4 dias). Conta sem foto e menção coletada antes de o
radar guardar o endereço da capa precisam de uma leitura nova: o profile scraper traz a foto e os 12 posts mais
recentes da conta (com capa), e o instagram-scraper traz, pelo link, a capa das menções que ficaram de fora desses 12.
Em seguida as imagens já são baixadas para data/capas/. `python -m radar imagens`.
"""
import json

from . import capas, config, monitoramento, perfis
from . import municipios as mun
from .descoberta import lotes

ATOR_LINKS = "apify/instagram-scraper"  # aceita o link de cada post (directUrls)
PRECO_PERFIL_1K = max(config.PRECO_PERFIL_1K, 2.6)  # o histórico dos brutos dá ~US$ 2,52 por mil perfis
PRECO_LINK_1K = max(config.PRECO_POST_1K, 2.7)
FOLGA = 1.5  # teto de cada execução: o custo estimado do lote com folga


def contas_sem_foto(con) -> list[dict]:
    """Contas que o painel mostra (as monitoradas e as que publicaram sobre a Energisa) sem foto baixada e sem
    endereço de foto que ainda valha."""
    tem = capas.existentes("perfis") | {u for u, url in con.execute(
        "SELECT username, foto FROM contas WHERE rede = 'instagram' AND foto IS NOT NULL") if capas.valida(url)}
    linhas = con.execute(
        "SELECT c.username, COALESCE(c.profundidade, 0) AS profundidade FROM contas c WHERE c.rede = 'instagram' "
        "AND (c.username IN (SELECT value FROM json_each(?)) OR c.username IN (SELECT p.username FROM posts p "
        "JOIN mencoes x ON x.rede = p.rede AND x.post_id = p.post_id AND x.sobre_energia = 1)) "
        "ORDER BY c.seguidores DESC", (json.dumps(capas.monitoradas(con)),)).fetchall()
    return [dict(l) for l in linhas if l["username"] not in tem]


def mencoes_sem_capa(con) -> list[dict]:
    """Menções à Energisa no feed sem capa baixada e sem endereço que ainda valha (stories têm a imagem do OCR)."""
    tem = capas.existentes("posts")
    linhas = con.execute(
        "SELECT p.post_id, p.username, p.url, p.capa FROM posts p JOIN mencoes x ON x.rede = p.rede "
        "AND x.post_id = p.post_id AND x.sobre_energia = 1 WHERE p.rede = 'instagram' "
        "AND COALESCE(p.tipo, '') != 'Story' AND p.url IS NOT NULL ORDER BY p.publicado_em DESC").fetchall()
    return [dict(l) for l in linhas if l["post_id"] not in tem and not capas.valida(l["capa"])]


def custo(n_contas: int, n_posts: int) -> float:
    return n_contas * PRECO_PERFIL_1K / 1000 + n_posts * PRECO_LINK_1K / 1000


def buscar(con, apify, contas: list[dict], mencoes: list[dict], aviso=print) -> dict:
    """Lê os perfis (foto e posts recentes) e, pelo link, as menções que continuarem sem endereço de capa."""
    municipios = mun.carregar(con)
    profundidade = {c["username"]: c.get("profundidade") or 0 for c in contas}
    resumo = {"perfis": 0, "posts": 0}
    for i, lote in enumerate(lotes(list(profundidade), config.PERFIS_POR_RUN), 1):
        aviso(f"Perfis: lote {i} ({len(lote)} contas)...")
        _, itens = apify.rodar(config.ATOR_PERFIL, {"usernames": lote}, "imagens_perfis",
                               max_usd=round(len(lote) * PRECO_PERFIL_1K / 1000 * FOLGA + 0.02, 2))
        for item in itens:
            username = (item.get("username") or "").lower()
            # A profundidade atual: a leitura não transforma a conta em semente de expansão.
            if perfis.ingerir(con, item, "monitor", municipios, profundidade.get(username, 0)):
                resumo["perfis"] += 1
        con.commit()
    faltam = [m for m in mencoes if not capas.valida(con.execute(
        "SELECT capa FROM posts WHERE rede = 'instagram' AND post_id = ?", (m["post_id"],)).fetchone()[0])]
    if faltam:
        aviso(f"Menções: {len(faltam)} posts pelo link...")
        _, itens = apify.rodar(ATOR_LINKS, {"directUrls": [m["url"] for m in faltam], "resultsType": "posts",
                                            "resultsLimit": 1, "addParentData": False}, "imagens_mencoes",
                               max_usd=round(len(faltam) * PRECO_LINK_1K / 1000 * FOLGA + 0.02, 2))
        for username, posts_da_conta in monitoramento.agrupar_por_dono(itens).items():
            resumo["posts"] += perfis.ingerir_posts(con, username, posts_da_conta)
        con.commit()
    return resumo


def baixar_agora(con, mencoes: list[dict], aviso=print) -> dict:
    """Baixa já as fotos e as capas das menções (o resto das capas fica para o painel, em segundo plano)."""
    ids = {m["post_id"] for m in mencoes}
    lista = [x for x in capas.pendentes(con) if x[0].parent.name == "perfis" or x[0].stem in ids]
    feitos = {"fotos": 0, "capas": 0, "falhas": 0}
    for destino, url, largura in lista:
        if capas.guardar(capas.baixar(url) or b"", destino, largura):
            feitos["fotos" if destino.parent.name == "perfis" else "capas"] += 1
        else:
            feitos["falhas"] += 1
    aviso(f"{feitos['fotos']} fotos e {feitos['capas']} capas de menções baixadas ({feitos['falhas']} falharam).")
    return feitos
