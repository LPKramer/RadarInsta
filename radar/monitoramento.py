"""Fase 2, coleta: posts das contas monitoradas e comentários dos posts sobre energia.

Os posts vêm do profile scraper (os 12 mais recentes de cada perfil). Sai bem mais barato por post que o post
scraper e ainda atualiza os seguidores, base da taxa de engajamento. Para não perder posts, a coleta precisa
rodar ao menos uma vez por dia: um portal grande chega a 12 posts num dia.
"""
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import config, db, municipios as mun, perfis
from .apify import tamanho_lote
from .descoberta import lotes

# Monitoradas: as A/B, as aprovadas pela equipe e as mais seguidas do TO (influentes.py), menos as rejeitadas.
# A IA ligou a conta ao TO: abrangência estadual ou algum município do TO.
SQL_DO_TO_PELA_IA = ("(k.abrangencia = 'estadual' OR EXISTS (SELECT 1 FROM conta_municipio cm WHERE cm.rede = c.rede "
                     "AND cm.username = c.username AND cm.fonte = 'ia'))")
LISTAS_MONITORADAS = ("mais_seguidas_to", "influenciadores_to")

# Monitoradas: prioridade A/B, aprovadas na revisão, as das listas (mais seguidas e influenciadores do TO) e todas
# as páginas de fofoca do TO, mesmo as de score baixo.
SQL_MONITORADAS = f"""
SELECT c.username, c.profundidade FROM contas c
LEFT JOIN score s ON s.rede = c.rede AND s.username = c.username
LEFT JOIN revisao r ON r.rede = c.rede AND r.username = c.username
LEFT JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username
WHERE c.rede = 'instagram' AND COALESCE(r.status, 'pendente') != 'rejeitada'
  AND (s.prioridade IN ('A', 'B') OR r.status = 'aprovada'
       OR EXISTS (SELECT 1 FROM listas l WHERE l.rede = c.rede AND l.username = c.username
                  AND l.lista IN ({', '.join(f"'{l}'" for l in LISTAS_MONITORADAS)}))
       OR (k.tipo = 'perfil_fofoca' AND c.descartada IS NULL AND COALESCE(c.privada, 0) = 0
           AND {SQL_DO_TO_PELA_IA}))
ORDER BY s.valor DESC, c.seguidores DESC
"""


def monitoradas(con, limite: int | None = None) -> list[dict]:
    contas = [dict(l) for l in con.execute(SQL_MONITORADAS)]
    return contas[:limite] if limite else contas


def custo(n_contas: int) -> float:
    return n_contas * config.PRECO_PERFIL_1K / 1000


def coletar(con, apify, contas: list[dict], aviso=print) -> dict:
    """Coleta o perfil (com os posts recentes) de cada conta. Devolve contagens."""
    municipios = mun.carregar(con)
    profundidade = {c["username"]: c["profundidade"] for c in contas}
    perfis_lidos = posts = 0
    for i, lote in enumerate(lotes(list(profundidade), tamanho_lote(apify, config.PERFIS_POR_RUN)), 1):
        aviso(f"Monitoramento: lote {i} ({len(lote)} contas)...")
        _, itens = apify.rodar(config.ATOR_PERFIL, {"usernames": lote}, "monitor")
        for item in itens:
            username = (item.get("username") or "").lower()
            # Passa a profundidade atual: a coleta não deve transformar a conta em semente de expansão.
            if perfis.ingerir(con, item, "monitor", municipios, profundidade.get(username, 0)):
                perfis_lidos += 1
                posts += len(item.get("latestPosts") or [])
        con.commit()
    return {"perfis": perfis_lidos, "posts": posts}


# --- Coleta completa pelo navegador -------------------------------------------------------
# O feed do navegador não tem o limite de 12 posts: cada conta é lida até o último post já guardado. Os posts dos
# últimos dias são relidos para atualizar curtidas e comentários, e o perfil (seguidores) é relido uma vez por semana.

JANELA_ATUALIZACAO = timedelta(days=3)
DIAS_PERFIL = 7
LIMITE_POSTS_MONITOR = 300


def _iso_z(data: datetime) -> str:
    return data.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ordem_de_visita(con, contas: list[dict]) -> list[dict]:
    """Nunca visitadas primeiro, depois as visitadas há mais tempo: uma rodada interrompida retoma por aí."""
    visitas = dict(con.execute("SELECT username, ultima_em FROM visitas WHERE rede = 'instagram'").fetchall())
    return sorted(contas, key=lambda c: visitas.get(c["username"]) or "")


def agrupar_por_dono(itens: list[dict]) -> dict[str, list[dict]]:
    por_conta = defaultdict(list)
    for item in itens:
        dono = (item.get("ownerUsername") or "").lower()
        if dono:
            por_conta[dono].append(item)
    return por_conta


def coletar_novos(con, coletor, contas: list[dict], aviso=print, agora: datetime | None = None,
                  com_stories: bool = True, triar=None) -> dict:
    """Visita todas as contas: posts novos (sem o limite de 12), engajamento dos últimos dias e, se a conta tem
    story novo, os stories (que somem em 24 horas)."""
    # Importado aqui: stories usa mencoes, que importa este módulo.
    from . import stories

    agora = agora or datetime.now(timezone.utc)
    municipios = mun.carregar(con)
    janela = agora - JANELA_ATUALIZACAO
    resumo = {"contas": 0, "posts": 0, "perfis": 0, "stories": 0, "stories_sobre_energia": 0}
    com_stories = com_stories and hasattr(coletor, "stories")
    fila = ordem_de_visita(con, contas)
    for i, lote in enumerate(lotes(fila, tamanho_lote(coletor, config.PERFIS_POR_RUN)), 1):
        usernames = [c["username"] for c in lote]
        # Rolar a grade custa leitura: só até o último post já gravado (stories não contam: somem e não estão na
        # grade). O engajamento dos posts dos últimos dias se atualiza com a primeira página, que vem com a visita.
        desde = {}
        for u in usernames:
            ultimo = perfis.ler_data(con.execute(
                "SELECT MAX(publicado_em) FROM posts WHERE rede = 'instagram' AND username = ? "
                "AND COALESCE(tipo, '') != ?", (u, stories.TIPO)).fetchone()[0])
            desde[u] = _iso_z(ultimo or janela)
        entrada = {"username": usernames, "onlyPostsNewerThan": min(*desde.values(), _iso_z(janela))[:10],
                   "resultsLimit": LIMITE_POSTS_MONITOR, "desde_por_conta": desde, "atualizar_desde": _iso_z(janela)}
        _, itens = coletor.rodar(config.ATOR_POSTS, entrada, "monitor_posts")
        por_conta = agrupar_por_dono(itens)
        for u in usernames:
            resumo["posts"] += perfis.ingerir_posts(con, u, por_conta.get(u, []))
        # Grava já: a leitura seguinte espera a pausa do ritmo, e outro trabalhador não pode ficar travado no banco.
        con.commit()

        # Seguidores mudam devagar: o perfil é atualizado uma vez por semana, com o que a visita acabou de trazer
        # (pelo navegador); só sem isso ele é lido de novo.
        velhas = [l[0] for l in con.execute(
            f"SELECT username FROM contas WHERE rede = 'instagram' AND username IN ({', '.join('?' * len(usernames))})"
            " AND (atualizada_em IS NULL OR atualizada_em < ?)",
            (*usernames, (agora - timedelta(days=DIAS_PERFIL)).isoformat(timespec="seconds")))]
        if velhas:
            profundidade = {c["username"]: c.get("profundidade", 0) for c in lote}
            vistos = {u: coletor.perfil_visto(u) for u in velhas} if hasattr(coletor, "perfil_visto") else {}
            perfis_lidos = [p for p in vistos.values() if p]
            faltam = [u for u in velhas if not vistos.get(u)]
            if faltam:
                perfis_lidos += coletor.rodar(config.ATOR_PERFIL, {"usernames": faltam}, "monitor")[1]
            for item in perfis_lidos:
                username = (item.get("username") or "").lower()
                if perfis.ingerir(con, item, "monitor", municipios, profundidade.get(username, 0)):
                    resumo["perfis"] += 1
            con.commit()

        for u in usernames if com_stories else []:
            if coletor.story_novo(u, stories.ultimo(con, u)):
                lidos = stories.coletar(con, coletor, u, triar=triar)
                resumo["stories"] += lidos["stories"]
                if lidos.get("triados"):
                    resumo["stories_triados"] = resumo.get("stories_triados", 0) + lidos["triados"]
                resumo["stories_sobre_energia"] += lidos["candidatos"]

        # A foto do perfil vem em toda visita pelo navegador. O endereço vence em ~4 dias: é renovado a cada uma, e o
        # painel baixa a imagem (capas.py).
        if hasattr(coletor, "foto_vista"):
            con.executemany("UPDATE contas SET foto = ? WHERE rede = 'instagram' AND username = ?",
                            [(foto, u) for u in usernames if (foto := coletor.foto_vista(u))])

        con.executemany("INSERT INTO visitas (rede, username, ultima_em) VALUES ('instagram', ?, ?) "
                        "ON CONFLICT (rede, username) DO UPDATE SET ultima_em = excluded.ultima_em",
                        [(u, agora.isoformat(timespec="seconds")) for u in usernames])
        con.commit()
        resumo["contas"] += len(usernames)
        if i % 10 == 0:
            aviso(f"Monitoramento: {resumo['contas']} de {len(fila)} contas, {resumo['posts']} posts...")
    return resumo


# --- Comentários ---------------------------------------------------------------------------

def custo_comentarios(n_posts: int, por_post: int) -> float:
    return n_posts * por_post * config.PRECO_COMENTARIO_1K / 1000


def ingerir_comentarios(con, itens: list[dict]) -> int:
    """Liga cada comentário ao post pelo shortcode da URL. Devolve quantos foram gravados."""
    gravados = 0
    for item in itens:
        shortcode = perfis.shortcode_de_url(item.get("postUrl"))
        post = con.execute("SELECT post_id FROM posts WHERE shortcode = ?", (shortcode,)).fetchone()
        if not post or not item.get("id") or not item.get("text"):
            continue
        con.execute(
            "INSERT OR IGNORE INTO comentarios (rede, comentario_id, post_id, texto, curtidas, publicado_em) "
            "VALUES ('instagram', ?, ?, ?, ?, ?)",
            (str(item["id"]), post["post_id"], item["text"], item.get("likesCount"), item.get("timestamp")),
        )
        gravados += 1
    return gravados


def coletar_comentarios(con, apify, posts: list[dict], por_post: int, aviso=print) -> int:
    """Coleta até `por_post` comentários de cada post. Devolve quantos foram gravados."""
    total = 0
    for i, lote in enumerate(lotes(posts, tamanho_lote(apify, 50)), 1):
        aviso(f"Comentários: lote {i} ({len(lote)} posts)...")
        entrada = {"directUrls": [p["url"] for p in lote], "resultsLimit": por_post}
        _, itens = apify.rodar(config.ATOR_COMENTARIOS, entrada, "comentarios")
        total += ingerir_comentarios(con, itens)
        con.commit()
    return total


# --- Histórico ------------------------------------------------------------------------------
# O profile scraper só traz os 12 últimos posts. Para olhar para trás, o post scraper traz todos os posts de um
# período e cobra por post. A estimativa usa o ritmo de cada conta, medido pelos 12 posts mais recentes já guardados.

CONTAS_POR_LOTE_HISTORICO = 10
# resultsLimit por conta = estimativa × FOLGA + MARGEM_POSTS, para não cortar o período de quem acelerou.
FOLGA = 2
MARGEM_POSTS = 30


def ritmo(con, username: str, agora: datetime | None = None) -> float:
    """Posts por dia, estimado pelos 12 posts mais recentes já guardados da conta."""
    datas = [datetime.fromisoformat(l[0].replace("Z", "+00:00")) for l in con.execute(
        "SELECT publicado_em FROM posts WHERE rede = 'instagram' AND username = ? AND publicado_em IS NOT NULL "
        "ORDER BY publicado_em DESC LIMIT 12", (username,))]
    if len(datas) < 2:
        return 0.0
    agora = agora or datetime.now(timezone.utc)
    return len(datas) / max((agora - datas[-1]).total_seconds() / 86400, 0.5)


def sem_historico(con, contas: list[dict], desde: str) -> list[dict]:
    """Contas cujo histórico ainda não chega a `desde` (AAAA-MM-DD)."""
    cobertas = {l[0] for l in con.execute(
        "SELECT username FROM historico WHERE rede = 'instagram' AND desde <= ?", (desde,))}
    return [c for c in contas if c["username"] not in cobertas]


def estimar_historico(con, contas: list[dict], dias: int) -> dict[str, int]:
    """Posts esperados de cada conta no período."""
    return {c["username"]: math.ceil(ritmo(con, c["username"]) * dias) for c in contas}


def custo_historico(n_posts: int) -> float:
    return n_posts * config.PRECO_POST_1K / 1000


def ingerir_historico(con, itens: list[dict], usernames: list[str], desde: str,
                      limite: int | None = None) -> dict[str, int]:
    """Grava os posts de cada conta e registra até onde o histórico dela chega. Devolve posts por conta.

    Contas sem nenhum post no período (ou privadas) também ficam registradas, para não serem cobradas de novo."""
    por_conta = agrupar_por_dono(itens)
    for username in usernames:
        posts = por_conta.get(username, [])
        perfis.ingerir_posts(con, username, posts)
        # Se a coleta bateu no limite de posts, o histórico só chega até o post mais antigo que veio.
        datas = [p["timestamp"][:10] for p in posts if p.get("timestamp")]
        cobre = min(datas) if limite and len(posts) >= limite and datas else desde
        con.execute(
            "INSERT INTO historico (rede, username, desde, n_posts, coletado_em) VALUES ('instagram', ?, ?, ?, ?) "
            "ON CONFLICT (rede, username) DO UPDATE SET desde = MIN(historico.desde, excluded.desde), "
            "n_posts = excluded.n_posts, coletado_em = excluded.coletado_em",
            (username, cobre, len(posts), db.agora()),
        )
    return {u: len(por_conta.get(u, [])) for u in usernames}


def coletar_historico(con, apify, contas: list[dict], desde: str, estimativa: dict[str, int],
                      max_usd: float | None = None, aviso=print) -> dict:
    """Coleta, em lotes, todos os posts publicados desde `desde`. Com `max_usd`, para de abrir lotes antes de o
    gasto passar do teto. Devolve contagens."""
    contas_feitas = posts = 0
    for i, lote in enumerate(lotes([c["username"] for c in contas], CONTAS_POR_LOTE_HISTORICO), 1):
        gasto = getattr(apify, "gasto_usd", 0.0)
        previsto = custo_historico(sum(estimativa.get(u, 0) for u in lote))
        if max_usd is not None and gasto + previsto > max_usd:
            aviso(f"Teto de US$ {max_usd:.2f}: lote {i} ficaria em ~US$ {previsto:.2f} e o gasto já é "
                  f"US$ {gasto:.2f}. Parando.")
            break
        limite = max(estimativa.get(u, 0) for u in lote) * FOLGA + MARGEM_POSTS
        # A Apify exige teto de pelo menos US$ 0,50 por execução.
        teto = max(0.5, 2 * previsto) if max_usd is None else max(0.5, min(2 * previsto, max_usd - gasto))
        aviso(f"Histórico: lote {i} ({len(lote)} contas, ~{sum(estimativa.get(u, 0) for u in lote)} posts)...")
        entrada = {"username": lote, "onlyPostsNewerThan": desde, "resultsLimit": limite}
        _, itens = apify.rodar(config.ATOR_POSTS, entrada, "historico", teto)
        por_conta = ingerir_historico(con, itens, lote, desde, limite)
        con.commit()
        contas_feitas += len(lote)
        posts += sum(por_conta.values())
    return {"contas": contas_feitas, "posts": posts, "gasto_usd": round(getattr(apify, "gasto_usd", 0.0), 2)}
