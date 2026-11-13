"""Converte itens de perfil da Apify (busca ou profile scraper) em linhas da tabela `contas`."""
import json
import re
from datetime import datetime, timedelta, timezone

from . import db, municipios as mun

N_POSTS_RECENTES = 6
TAM_LEGENDA = 300
RE_USERNAME_URL = re.compile(r"instagram\.com/([A-Za-z0-9_.]{1,30})(?:[/?#]|$)")
RE_USERNAME_TITULO = re.compile(r"\(@([A-Za-z0-9_.]{1,30})\)")
RE_SHORTCODE_URL = re.compile(r"instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
CAMINHOS_RESERVADOS = {"p", "reel", "reels", "explore", "stories", "tv", "accounts", "direct", "tags"}


def ler_data(valor) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None


def _resumo_posts(posts: list[dict], referencia: datetime) -> dict:
    """Último post, engajamento médio, posts em 30 dias e as legendas mais recentes."""
    datados = [(p, ler_data(p.get("timestamp"))) for p in posts]
    datados = sorted((x for x in datados if x[1]), key=lambda x: x[1], reverse=True)
    if not datados:
        return {"ultimo_post_em": None, "engajamento_medio": None, "posts_30d": None, "posts_recentes": None}

    # O Instagram devolve -1 quando o perfil esconde as curtidas.
    interacoes = [max(p.get("likesCount") or 0, 0) + max(p.get("commentsCount") or 0, 0) for p, _ in datados]
    limite_30d = referencia - timedelta(days=30)
    recentes = [
        {"data": d.date().isoformat(), "legenda": (p.get("caption") or "")[:TAM_LEGENDA],
         "curtidas": p.get("likesCount"), "comentarios": p.get("commentsCount")}
        for p, d in datados[:N_POSTS_RECENTES]
    ]
    return {
        "ultimo_post_em": datados[0][1].isoformat(timespec="seconds"),
        "engajamento_medio": sum(interacoes) / len(interacoes),
        "posts_30d": sum(1 for _, d in datados if d >= limite_30d),
        "posts_recentes": json.dumps(recentes, ensure_ascii=False),
    }


def normalizar(item: dict, referencia: datetime | None = None) -> dict | None:
    """Item da Apify -> dicionário no formato de `contas`. None se não for um perfil."""
    username = (item.get("username") or "").strip().lower()
    if not username or item.get("error"):
        return None
    referencia = referencia or datetime.now(timezone.utc)
    conta = {
        "username": username,
        "ig_id": str(item["id"]) if item.get("id") else None,
        "nome": item.get("fullName"),
        "bio": item.get("biography"),
        "seguidores": item.get("followersCount"),
        "seguindo": item.get("followsCount"),
        "n_posts": item.get("postsCount"),
        "privada": _bool(item.get("private")),
        "verificada": _bool(item.get("verified")),
        "business": _bool(item.get("isBusinessAccount")),
        "categoria": item.get("businessCategoryName"),
        "url_externa": item.get("externalUrl"),
        "foto": item.get("profilePicUrlHD") or item.get("profilePicUrl") or None,
        "local_perfil": item.get("location") or None,  # o X tem o campo; o Instagram, não
        "completo": item.get("followersCount") is not None,
    }
    conta.update(_resumo_posts(item.get("latestPosts") or [], referencia))
    return conta


def _bool(valor) -> int | None:
    return None if valor is None else int(bool(valor))


def relacionadas(item: dict) -> list[tuple[str, str | None]]:
    """(username, nome) dos perfis públicos que o Instagram sugere como parecidos com este."""
    return [
        (r["username"].lower(), r.get("full_name") or None) for r in item.get("relatedProfiles") or []
        if r.get("username") and not r.get("is_private")
    ]


def ingerir(con, item: dict, origem: str, municipios, profundidade: int = 0, rede: str = "instagram") -> str | None:
    """Grava o perfil, as pistas de município (bio, local do perfil e @) e as contas relacionadas. Devolve o
    username. Os perfis do Threads e do X chegam no mesmo formato (redes.py)."""
    conta = normalizar(item)
    if conta is None:
        return None
    db.upsert_conta(con, {**conta, "origem": origem, "profundidade": profundidade}, rede)

    texto = f"{conta.get('nome') or ''} {conta.get('bio') or ''} {conta.get('local_perfil') or ''}"
    for ibge_id in mun.no_texto(texto, municipios) | mun.no_username(conta["username"], municipios):
        db.vincular_municipio(con, conta["username"], ibge_id, "bio", rede)

    for rel, nome in relacionadas(item):
        con.execute("INSERT INTO relacionadas (rede, origem, relacionada, nome) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT DO UPDATE SET nome = COALESCE(excluded.nome, relacionadas.nome)",
                    (rede, conta["username"], rel, nome))
    ingerir_posts(con, conta["username"], item.get("latestPosts") or [], rede)
    return conta["username"]


def shortcode_de_url(url: str) -> str | None:
    achado = RE_SHORTCODE_URL.search(url or "")
    return achado.group(1) if achado else None


def ingerir_posts(con, username: str, posts: list[dict], rede: str = "instagram") -> int:
    """Grava os posts e atualiza o engajamento dos já conhecidos (fica a maior contagem vista)."""
    def contagem(valor):
        return valor if isinstance(valor, int) and valor >= 0 else None  # -1 = curtidas ocultas

    medido_em = db.agora()
    posts = [p for p in posts if p.get("id") or p.get("shortCode")]
    for p in posts:
        con.execute(
            """INSERT INTO posts (rede, post_id, username, shortcode, url, tipo, publicado_em, legenda, curtidas,
                                  comentarios, visualizacoes, local, medido_em, capa)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (rede, post_id) DO UPDATE SET
                 legenda = COALESCE(excluded.legenda, posts.legenda),
                 capa = COALESCE(excluded.capa, posts.capa),
                 curtidas = MAX(COALESCE(excluded.curtidas, posts.curtidas), COALESCE(posts.curtidas, excluded.curtidas)),
                 comentarios = MAX(COALESCE(excluded.comentarios, posts.comentarios),
                                   COALESCE(posts.comentarios, excluded.comentarios)),
                 visualizacoes = MAX(COALESCE(excluded.visualizacoes, posts.visualizacoes),
                                     COALESCE(posts.visualizacoes, excluded.visualizacoes)),
                 medido_em = excluded.medido_em""",
            (rede, str(p.get("id") or p.get("shortCode")), username,
             p.get("shortCode") or shortcode_de_url(p.get("url")),
             p.get("url"), p.get("type"), p.get("timestamp"), p.get("caption"), contagem(p.get("likesCount")),
             contagem(p.get("commentsCount")), contagem(p.get("videoViewCount")), p.get("locationName"), medido_em,
             p.get("displayUrl") or None),
        )
    return len(posts)


def username_de_url(url: str) -> str | None:
    """'https://www.instagram.com/gurupinoticias/' -> 'gurupinoticias' (ignora links de post/reel)."""
    achado = RE_USERNAME_URL.search(url or "")
    if not achado or achado.group(1).lower() in CAMINHOS_RESERVADOS:
        return None
    return achado.group(1).lower()


def username_do_titulo(titulo: str) -> str | None:
    """'Alvorada Notícias (@alvorada_noticias) • Instagram' -> 'alvorada_noticias'."""
    achado = RE_USERNAME_TITULO.search(titulo or "")
    return achado.group(1).lower() if achado else None


def ingerir_username(con, username: str, origem: str, profundidade: int = 0) -> bool:
    """Registra só o @ (sem dados de perfil). Devolve True se a conta era nova."""
    existia = con.execute("SELECT 1 FROM contas WHERE rede = 'instagram' AND username = ?",
                          (username,)).fetchone()
    db.upsert_conta(con, {"username": username, "origem": origem, "profundidade": profundidade})
    return existia is None
