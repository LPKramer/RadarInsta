"""Etapa 2 (descoberta de candidatas) e expansão por bola de neve."""
import re
from collections import Counter

from . import config, db, municipios as mun, perfis, termos as tm
from .classificacao import SQL_RELEVANTE
from .termos import Termo


def lotes(seq: list, tamanho: int):
    for i in range(0, len(seq), tamanho):
        yield seq[i:i + tamanho]


def filtrar_municipios(municipios, nome: str | None):
    if not nome:
        return municipios
    alvo = mun.casar_nome(nome, municipios)
    if alvo is None:
        raise ValueError(f"Município não encontrado no Tocantins: {nome}")
    return [m for m in municipios if m["ibge_id"] == alvo]


# --- Busca de perfis do Instagram ---------------------------------------------------------

def pouca_cobertura(con, municipios, maximo: int) -> list[dict]:
    """Municípios com no máximo `maximo` contas A/B (sem contar as rejeitadas pela equipe)."""
    contagem = dict(con.execute(
        """SELECT cm.ibge_id, COUNT(DISTINCT cm.username) FROM conta_municipio cm
           JOIN score s ON s.rede = cm.rede AND s.username = cm.username
           LEFT JOIN revisao r ON r.rede = cm.rede AND r.username = cm.username
           WHERE cm.fonte = 'ia' AND s.prioridade IN ('A', 'B') AND COALESCE(r.status, '') != 'rejeitada'
           GROUP BY 1""").fetchall())
    return [m for m in municipios if contagem.get(m["ibge_id"], 0) <= maximo]


def planejar_busca(con, municipio: str | None = None, alvo: list[dict] | None = None) -> list[Termo]:
    """Termos ainda não executados. Termos estaduais só entram quando o alvo é o estado inteiro."""
    municipios = mun.carregar(con)
    if not municipios:
        raise RuntimeError("Tabela de municípios vazia. Rode primeiro: python -m radar municipios")
    if alvo is None:
        alvo = filtrar_municipios(municipios, municipio)
    lista = tm.gerar(alvo, incluir_estado=len(alvo) == len(municipios))
    feitos = db.termos_executados(con, "busca")
    return [t for t in lista if t.termo not in feitos]


def custo_busca(n_termos: int, search_limit: int = config.SEARCH_LIMIT) -> float:
    """Teto estimado: supõe que todo termo devolve o limite de resultados."""
    return n_termos * search_limit * config.PRECO_BUSCA_1K / 1000


def ingerir_busca(con, itens: list[dict], por_termo: dict[str, Termo], municipios) -> Counter:
    contagem = Counter()
    for item in itens:
        termo = por_termo.get(mun.normalizar(item.get("searchTerm")))
        username = perfis.ingerir(con, item, "busca", municipios)
        if username and termo:
            contagem[termo.termo] += 1
            if termo.ibge_id:
                db.vincular_municipio(con, username, termo.ibge_id, "busca")
    return contagem


def executar_busca(con, apify, termos: list[Termo], search_limit: int = config.SEARCH_LIMIT,
                   aviso=print) -> int:
    municipios = mun.carregar(con)
    por_termo = {mun.normalizar(t.termo): t for t in termos}
    total = 0
    for i, lote in enumerate(lotes(termos, config.TERMOS_POR_RUN), 1):
        aviso(f"Busca: lote {i} ({len(lote)} termos)...")
        entrada = {"search": ", ".join(t.termo for t in lote), "searchType": "user",
                   "searchLimit": search_limit}
        run_id, itens = apify.rodar(config.ATOR_BUSCA, entrada, "busca")
        contagem = ingerir_busca(con, itens, por_termo, municipios)
        for t in lote:
            db.registrar_busca(con, "busca", t.termo, t.ibge_id, run_id, contagem.get(t.termo, 0))
        con.commit()
        total += len(itens)
    return total


# --- Fonte opcional: autores frequentes em hashtags locais ---------------------------------

def custo_hashtags(n_tags: int, por_tag: int) -> float:
    return n_tags * por_tag * config.PRECO_HASHTAG_1K / 1000


def ingerir_hashtags(con, itens: list[dict], min_ocorrencias: int = 2) -> tuple[int, Counter]:
    """Registra quem posta com frequência nas hashtags. Devolve (contas novas, posts por hashtag)."""
    autores = Counter((i.get("ownerUsername") or "").lower() for i in itens)
    autores.pop("", None)
    por_tag = Counter(h.lower() for i in itens for h in set(i.get("hashtags") or []))
    novas = sum(perfis.ingerir_username(con, u, "hashtag") for u, n in autores.items() if n >= min_ocorrencias)
    return novas, por_tag


def executar_hashtags(con, apify, tags: list[str], por_tag: int = 100, min_ocorrencias: int = 2) -> int:
    feitos = db.termos_executados(con, "hashtag")
    tags = [t for t in tags if t not in feitos]
    if not tags:
        return 0
    entrada = {"hashtags": tags, "resultsType": "posts", "resultsLimit": por_tag}
    run_id, itens = apify.rodar(config.ATOR_HASHTAG, entrada, "hashtag")
    novas, contagem = ingerir_hashtags(con, itens, min_ocorrencias)
    for t in tags:
        db.registrar_busca(con, "hashtag", t, None, run_id, contagem.get(t, 0))
    con.commit()
    return novas


# --- Fonte opcional: Google com site:instagram.com ---------------------------------------

def custo_google(n_consultas: int, paginas: int = 1) -> float:
    """A Apify cobra por página de resultados."""
    return n_consultas * paginas * config.PRECO_GOOGLE_1K / 1000


def planejar_google(con, alvo: list[dict]) -> list[Termo]:
    """Uma consulta por município: a primeira de MODELOS_GOOGLE que ainda não rodou."""
    feitos = db.termos_executados(con, "google")
    proximas = {}
    for c in tm.consultas_google(alvo):
        if c.termo not in feitos:
            proximas.setdefault(c.ibge_id, c)
    return list(proximas.values())


def planejar_google_estado(con, municipios: list[dict]) -> list[Termo]:
    """Consultas estaduais, regionais e das cidades grandes que ainda não rodaram."""
    feitos = db.termos_executados(con, "google")
    return [c for c in tm.consultas_google_estado(municipios) if c.termo not in feitos]


def ingerir_google(con, itens: list[dict], por_consulta: dict[str, Termo]) -> tuple[int, Counter]:
    novas, contagem = 0, Counter()
    for item in itens:
        consulta = por_consulta.get((item.get("searchQuery") or {}).get("term"))
        for resultado in item.get("organicResults") or []:
            # Posts chegam como link de redirecionamento do Google; o autor fica no título: "Nome (@usuario)".
            username = perfis.username_de_url(resultado.get("url")) or perfis.username_do_titulo(resultado.get("title"))
            if not username:
                continue
            novas += perfis.ingerir_username(con, username, "google")
            if consulta:
                contagem[consulta.termo] += 1
                if consulta.ibge_id:
                    db.vincular_municipio(con, username, consulta.ibge_id, "busca")
    return novas, contagem


def executar_google(con, apify, consultas: list[Termo], aviso=print, paginas: int = 1) -> int:
    feitos = db.termos_executados(con, "google")
    consultas = [c for c in consultas if c.termo not in feitos]
    novas = 0
    for i, lote in enumerate(lotes(consultas, config.TERMOS_POR_RUN), 1):
        aviso(f"Google: lote {i} ({len(lote)} consultas)...")
        entrada = {"queries": "\n".join(c.termo for c in lote), "maxPagesPerQuery": paginas,
                   "countryCode": "br", "languageCode": "pt-BR"}
        run_id, itens = apify.rodar(config.ATOR_GOOGLE, entrada, "google")
        n, contagem = ingerir_google(con, itens, {c.termo: c for c in lote})
        for c in lote:
            db.registrar_busca(con, "google", c.termo, c.ibge_id, run_id, contagem.get(c.termo, 0))
        con.commit()
        novas += n
    return novas


# --- Bola de neve --------------------------------------------------------------------------

# Sementes da bola de neve: portais e páginas. No piloto, só 3% dos relacionados a políticos eram relevantes, e os
# relacionados a prefeituras eram quase todos outras prefeituras e secretarias.
TIPOS_SEMENTE = ["portal_noticias", "perfil_fofoca", "jornalista_radialista", "policial_plantao",
                 "pagina_cidade_comunidade"]


def expandir(con, confianca_min: float = config.CONFIANCA_EXPANSAO,
             profundidade_max: int = config.PROFUNDIDADE_MAX, tipos: list[str] | None = TIPOS_SEMENTE) -> int:
    """Traz para a base os perfis relacionados às contas relevantes. Devolve quantas contas novas.

    `tipos=None` aceita sementes de qualquer tipo relevante."""
    filtro_tipo = f" AND k.tipo IN ({', '.join('?' * len(tipos))})" if tipos else ""
    sementes = con.execute(
        f"""SELECT c.username, c.profundidade FROM contas c
            JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username
            LEFT JOIN revisao r ON r.rede = c.rede AND r.username = c.username
            WHERE {SQL_RELEVANTE} AND k.confianca >= ? AND c.profundidade < ?{filtro_tipo}
              AND COALESCE(r.status, 'pendente') != 'rejeitada'""",
        (confianca_min, profundidade_max, *(tipos or [])),
    ).fetchall()
    novas = 0
    for s in sementes:
        for (rel,) in con.execute("SELECT relacionada FROM relacionadas WHERE rede = 'instagram' AND origem = ?",
                                  (s["username"],)).fetchall():
            novas += perfis.ingerir_username(con, rel, "relacionada", s["profundidade"] + 1)
    con.commit()
    return novas


# No @ ou no nome do perfil: página de notícia, plantão, fofoca, rádio ou agenda da cidade.
RE_NOTICIA = re.compile(r"noticia|news|portal|plantao|policial|acontece|fofoca|jornal|radio|online|agora|urgente|"
                        r"informa|imprensa|reporter|alerta")


def candidatas_por_sinais(con, municipios, min_ligacoes: int = 2,
                          tipos: list[str] | None = TIPOS_SEMENTE) -> list[dict]:
    """Relacionados de qualquer conta da base que ainda não entraram nela, mas têm sinal de notícia local.

    Entram os apontados por `min_ligacoes` ou mais contas relevantes (por padrão, só portais e páginas contam: as
    ligações de prefeituras e políticos trazem outras prefeituras, lojas e eventos), e os que têm palavra de notícia
    no @ ou no nome e, além disso, são apontados por uma dessas contas ou citam um município do Tocantins. Ordem:
    mais ligações primeiro. A profundidade é a da origem mais rasa mais um."""
    filtro_tipo = f" AND k.tipo IN ({', '.join('?' * len(tipos))})" if tipos else ""
    relevantes = {l[0] for l in con.execute(
        f"""SELECT k.username FROM classificacoes k
            LEFT JOIN revisao r ON r.rede = k.rede AND r.username = k.username
            WHERE {SQL_RELEVANTE} AND COALESCE(r.status, 'pendente') != 'rejeitada'{filtro_tipo}""", tipos or [])}
    candidatas = {}
    for l in con.execute(
            """SELECT r.relacionada, r.nome, r.origem, o.profundidade FROM relacionadas r
               JOIN contas o ON o.rede = r.rede AND o.username = r.origem
               LEFT JOIN contas c ON c.rede = r.rede AND c.username = r.relacionada
               WHERE r.rede = 'instagram' AND c.username IS NULL"""):
        cand = candidatas.setdefault(l["relacionada"], {"username": l["relacionada"], "nome": None,
                                                         "ligacoes": set(), "profundidade": l["profundidade"] + 1})
        cand["nome"] = cand["nome"] or l["nome"]
        cand["profundidade"] = min(cand["profundidade"], l["profundidade"] + 1)
        if l["origem"] in relevantes:
            cand["ligacoes"].add(l["origem"])

    escolhidas = []
    for c in candidatas.values():
        n = len(c["ligacoes"])
        noticia = bool(RE_NOTICIA.search(mun.compactar(f"{c['username']} {c['nome'] or ''}")))
        if n >= min_ligacoes or (noticia and (n >= 1 or mun.no_texto(c["nome"] or "", municipios)
                                              or mun.no_username(c["username"], municipios))):
            escolhidas.append({"username": c["username"], "nome": c["nome"], "ligacoes": n,
                               "profundidade": c["profundidade"]})
    return sorted(escolhidas, key=lambda c: (-c["ligacoes"], c["username"]))
