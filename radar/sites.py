"""Sites de notícia lidos direto pelo feed: os jornais e portais do TO, os investigativos e as páginas das rádios que
publicam as suas matérias. Completa o Google Notícias (noticias.py), que não indexa boa parte dos sites pequenos.

A base (tabela `sites`) junta:
- a curadoria (radar/dados/sites_to.csv): os jornais e investigativos do TO e os investigativos nacionais;
- os veículos que o Google Notícias já trouxe;
- os sites das contas de notícia, rádio e jornalista ligadas ao TO no Instagram (o link da bio).
`descobrir` acha o feed de cada um: o RSS anunciado na página, os endereços comuns (WordPress, Blogger) ou a API do
WordPress. Site sem feed fica anotado e não é lido. As contas e o site da própria Energisa ficam de fora.

Cada rodada (`rodada`; na vigilância, a cada hora, um trabalhador por vez) lê os feeds e grava só as matérias
recentes que passam no filtro de energia, como as do Google Notícias: rede 'web', o domínio é a "conta" e a matéria é
o "post", com o título e o começo do texto. Site de escopo nacional só entra com matéria que cita o Tocantins. A
cada 6 h a rodada também roda o Google Notícias. A DeepSeek analisa, a matéria negativa vira alerta e a consolidação
(fatos.py) junta a mesma notícia de vários sites e perfis.
"""
from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

from . import db, mencoes, municipios as mun, noticias, perfis
from .monitoramento import SQL_DO_TO_PELA_IA

ARQUIVO_CURADO = Path(__file__).resolve().parent / "dados" / "sites_to.csv"
REDE = noticias.REDE  # 'web'
ORIGEM = "sites"
INTERVALO = timedelta(hours=1)
GOOGLE_A_CADA = timedelta(hours=6)
DIAS = 14
REVERIFICAR = timedelta(days=30)
TAM_TEXTO = 1500
MAX_BYTES = 3_000_000
TEMPO = 15
CAMINHOS_FEED = ("/feed/", "/rss", "/feed", "/rss.xml", "/?feed=rss2", "/feeds/posts/default?alt=rss")
API_WP = "/wp-json/wp/v2/posts?per_page=20&_fields=id,date_gmt,link,title,excerpt"
# Links de bio que não são site de notícia (redes, encurtadores, agregadores de links, lojas, apps).
DOMINIOS_FORA = (
    "instagram.com", "facebook.com", "fb.com", "fb.me", "wa.me", "whatsapp.com", "youtube.com", "youtu.be", "bit.ly",
    "tiktok.com", "twitter.com", "x.com", "threads.net", "threads.com", "t.me", "linktr.ee", "linkin.bio", "beacons.ai",
    "taplink.cc", "bio.link", "bio.site", "lnk.bio", "linkme.bio", "linkbio.co", "allmylinks.com", "solo.to",
    "carrd.co", "msha.ke", "goo.gl", "google.com", "forms.gle", "ig.me", "spotify.com", "soundcloud.com", "kwai.com",
    "canva.com", "canva.site", "wa.link", "acesse.one", "w.app", "globoplay.globo.com", "hotmart.com",
    "netlify.app", "lovable.app", "vercel.app", "wixsite.com", "streema.com", "zeno.fm", "cutt.ly", "tinyurl.com",
    "encurtador.com.br", "onelink.me", "apple.com")
SQL_BIOS = f"""
SELECT c.username, c.nome, c.url_externa,
       EXISTS (SELECT 1 FROM radios r WHERE r.rede = c.rede AND r.username = c.username AND r.do_to = 1
               AND r.categoria != 'nao') AS radio
FROM contas c LEFT JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username
WHERE c.rede = 'instagram' AND c.url_externa IS NOT NULL AND c.descartada IS NULL AND c.username NOT LIKE '%energisa%'
  AND ((k.tipo IN ('portal_noticias', 'pagina_cidade_comunidade', 'policial_plantao', 'jornalista_radialista')
        AND {SQL_DO_TO_PELA_IA})
       OR EXISTS (SELECT 1 FROM radios r WHERE r.rede = c.rede AND r.username = c.username AND r.do_to = 1
                  AND r.categoria != 'nao'))
"""
RE_TAG_LINK = re.compile(r"<link\b[^>]*>", re.I)
RE_TAG = re.compile(r"<[^>]+>")
NS = {"atom": "http://www.w3.org/2005/Atom", "content": "http://purl.org/rss/1.0/modules/content/"}


# --- A base ---------------------------------------------------------------------------------

def dominio(url: str | None) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    host = urllib.parse.urlsplit(url if "://" in url else f"http://{url}").hostname or ""
    return host.lower().removeprefix("www.") or None


def fora(host: str) -> bool:
    return "energisa" in host or any(host == d or host.endswith("." + d) for d in DOMINIOS_FORA)


def lista_curada() -> list[dict]:
    with open(ARQUIVO_CURADO, encoding="utf-8") as arquivo:
        return list(csv.DictReader(arquivo))


def base_candidata(con) -> dict[str, dict]:
    """domínio -> {nome, tipo, escopo, origem, conta, feed}. A curadoria vale primeiro."""
    sites = {}
    for r in lista_curada():
        sites[r["dominio"]] = {"nome": r["nome"], "tipo": r["tipo"], "escopo": r["escopo"] or "to",
                               "origem": "curadoria", "conta": None, "feed": r["feed"] or None}
    # Os veículos do Google Notícias podem ser de fora (só entraram com título sobre o TO): escopo nacional.
    for d, nome in con.execute("SELECT username, nome FROM contas WHERE rede = ? AND descartada IS NULL", (REDE,)):
        if not fora(d):
            sites.setdefault(d, {"nome": nome, "tipo": "jornal", "escopo": "nacional", "origem": "google_noticias",
                                 "conta": None, "feed": None})
    for l in con.execute(SQL_BIOS):
        d = dominio(l["url_externa"])
        if d and not fora(d) and "." in d:
            sites.setdefault(d, {"nome": l["nome"], "tipo": "radio" if l["radio"] else "portal", "escopo": "to",
                                 "origem": "bio_instagram", "conta": l["username"], "feed": None})
    return sites


# --- Rede ------------------------------------------------------------------------------------

def baixar(url: str, tempo: float = TEMPO) -> bytes:
    pedido = urllib.request.Request(url, headers={"User-Agent": noticias.AGENTE, "Accept": "*/*",
                                                  "Accept-Language": "pt-BR,pt;q=0.9"})
    with urllib.request.urlopen(pedido, timeout=tempo, context=noticias._contexto_tls()) as resposta:
        return resposta.read(MAX_BYTES)


def _texto(html: str | None) -> str:
    return " ".join(unescape(RE_TAG.sub(" ", unescape(html or ""))).split())


def _data(valor: str | None) -> str | None:
    if not valor:
        return None
    try:
        data = parsedate_to_datetime(valor)
    except (TypeError, ValueError, IndexError):
        data = perfis.ler_data(valor.strip())
    if data is None:
        return None
    data = data if data.tzinfo else data.replace(tzinfo=timezone.utc)
    return data.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ler_feed(conteudo: bytes) -> list[dict]:
    """RSS 2.0 ou Atom -> [{id, titulo, link, publicado_em, texto}]. O texto vem do conteúdo completo, quando o feed
    traz (content:encoded), ou do resumo."""
    raiz = ET.fromstring(conteudo)
    itens = []
    for item in raiz.iter("item"):
        link = (item.findtext("link") or "").strip()
        corpo = item.findtext("content:encoded", namespaces=NS) or item.findtext("description")
        itens.append({"id": link or (item.findtext("guid") or "").strip(), "titulo": _texto(item.findtext("title")),
                      "link": link, "texto": _texto(corpo)[:TAM_TEXTO],
                      "publicado_em": _data(item.findtext("pubDate")
                                            or item.findtext("{http://purl.org/dc/elements/1.1/}date"))})
    for entrada in raiz.iter("{http://www.w3.org/2005/Atom}entry"):
        alternativo = next((l for l in entrada.findall("atom:link", NS) if l.get("rel", "alternate") == "alternate"),
                           None)
        link = alternativo.get("href", "") if alternativo is not None else ""
        corpo = entrada.findtext("atom:content", namespaces=NS) or entrada.findtext("atom:summary", namespaces=NS)
        itens.append({"id": link or (entrada.findtext("atom:id", namespaces=NS) or ""),
                      "titulo": _texto(entrada.findtext("atom:title", namespaces=NS)), "link": link,
                      "texto": _texto(corpo)[:TAM_TEXTO],
                      "publicado_em": _data(entrada.findtext("atom:published", namespaces=NS)
                                            or entrada.findtext("atom:updated", namespaces=NS))})
    return [i for i in itens if i["id"] and i["titulo"]]


def ler_wp(conteudo: bytes) -> list[dict]:
    """A API de posts do WordPress -> o mesmo formato de `ler_feed`."""
    dados = json.loads(conteudo)
    return [{"id": p["link"], "titulo": _texto((p.get("title") or {}).get("rendered")), "link": p["link"],
             "texto": _texto((p.get("excerpt") or {}).get("rendered"))[:TAM_TEXTO],
             "publicado_em": _data(f"{p['date_gmt']}+00:00" if p.get("date_gmt") else None)}
            for p in dados if isinstance(p, dict) and p.get("link")] if isinstance(dados, list) else []


def feeds_anunciados(html: str, base: str) -> list[str]:
    """Os feeds que a página anuncia (<link rel="alternate" type="application/rss+xml">), menos os de comentários."""
    urls = []
    for tag in RE_TAG_LINK.findall(html[:300_000]):
        if re.search(r"alternate", tag, re.I) and re.search(r"(rss|atom)\+xml", tag, re.I):
            href = re.search(r"href\s*=\s*[\"']([^\"']+)", tag, re.I)
            if href:
                urls.append(urllib.parse.urljoin(base, unescape(href.group(1))))
    return [u for u in dict.fromkeys(urls) if not re.search(r"coment|comment", u, re.I)]


def achar_feed(dominio_: str, baixar_=baixar, sugerido: str | None = None) -> tuple[str | None, str | None, str | None]:
    """(formato, feed, erro): o primeiro que responde com matérias, entre o sugerido, o anunciado na página, os
    endereços comuns e a API do WordPress; no domínio e, se nada responder, no www (alguns sites só atendem nele)."""
    formato, feed, erro = _achar_feed(f"https://{dominio_}", baixar_, sugerido)
    if feed or dominio_.count(".") > 2:  # subdomínio (g1.globo.com): sem www
        return formato, feed, erro
    return _achar_feed(f"https://www.{dominio_}", baixar_, None)


def _achar_feed(base: str, baixar_, sugerido: str | None) -> tuple[str | None, str | None, str | None]:
    tentativas = [sugerido] if sugerido else []
    try:
        tentativas += feeds_anunciados(baixar_(base + "/").decode("utf-8", "replace"), base + "/")
    except urllib.error.HTTPError:
        pass  # a página pode recusar e o feed responder
    except Exception as erro:  # sem DNS ou sem conexão: nem tenta o resto
        if not sugerido:
            return None, None, type(erro).__name__
    for url in dict.fromkeys(tentativas + [base + c for c in CAMINHOS_FEED]):
        try:
            if ler_feed(baixar_(url)):
                return "rss", url, None
        except Exception:
            continue
    try:
        if ler_wp(baixar_(base + API_WP)):
            return "wp_json", base + API_WP, None
    except Exception:
        pass
    return None, None, "sem feed"


def descobrir(con, aviso=print, baixar_=baixar, reverificar: bool = False, agora: datetime | None = None,
              paralelo: int = 8) -> Counter:
    """Atualiza a base e acha o feed dos sites novos (e dos verificados há mais de 30 dias)."""
    agora = agora or datetime.now(timezone.utc)
    base = base_candidata(con)
    verificados = dict(con.execute("SELECT dominio, verificado_em FROM sites").fetchall())
    a_verificar = []
    for d, info in base.items():
        con.execute(
            "INSERT INTO sites (dominio, nome, tipo, escopo, origem, conta) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (dominio) DO UPDATE SET nome = excluded.nome, tipo = excluded.tipo, escopo = excluded.escopo, "
            "origem = excluded.origem, conta = COALESCE(excluded.conta, sites.conta)",
            (d, info["nome"], info["tipo"], info["escopo"], info["origem"], info["conta"]))
        quando = perfis.ler_data(verificados.get(d))
        if reverificar or quando is None or agora - quando > REVERIFICAR:
            a_verificar.append((d, info.get("feed")))
    con.commit()
    resumo = Counter(base=len(base), verificados=len(a_verificar))

    def achar(par):  # roda em outra thread: só rede
        return achar_feed(par[0], baixar_, par[1])

    with ThreadPoolExecutor(paralelo) as executor:
        for i, ((d, _), (formato, feed, erro)) in enumerate(zip(a_verificar, executor.map(achar, a_verificar)), 1):
            con.execute("UPDATE sites SET formato = ?, feed = ?, erro = ?, verificado_em = ? WHERE dominio = ?",
                        (formato, feed, erro, db.agora(), d))
            con.commit()  # já: o próximo resultado espera a rede, e a vigilância não pode ficar sem o banco
            resumo["com_feed" if feed else "sem_feed"] += 1
            if i % 25 == 0:
                aviso(f"{i} de {len(a_verificar)} sites verificados; {resumo['com_feed']} com feed")
    con.commit()
    return resumo


# --- Leitura ---------------------------------------------------------------------------------

def _chave(titulo: str | None) -> str:
    return mun.normalizar((titulo or "").split("\n", 1)[0])[:150]


def ingerir(con, site: dict, itens: list[dict], municipios, agora: datetime, dias: int = DIAS) -> Counter:
    """Grava as matérias recentes do site que passam no filtro de energia (e, no escopo nacional, que citam o TO).
    A mesma matéria já vinda do Google Notícias (mesmo site, mesmo título) não entra de novo."""
    resumo = Counter()
    limite = agora - timedelta(days=dias)
    vistas = {_chave(l[0]) for l in con.execute("SELECT legenda FROM posts WHERE rede = ? AND username = ?",
                                                 (REDE, site["dominio"]))}
    for item in itens:
        data = perfis.ler_data(item["publicado_em"])
        texto = f"{item['titulo']} {item['texto']}"
        if data is None or data < limite:
            resumo["antigas"] += 1
        elif not mencoes.menciona_energia(texto):
            resumo["sem_energia"] += 1
        elif noticias.RE_OUTRA_DISTRIBUIDORA.search(mun.normalizar(item["titulo"])):
            resumo["outra_distribuidora"] += 1
        elif site["escopo"] == "nacional" and not noticias.do_tocantins(texto, municipios):
            resumo["fora_do_to"] += 1
        elif _chave(item["titulo"]) in vistas:
            resumo["repetidas"] += 1
        else:
            db.upsert_conta(con, {"username": site["dominio"], "nome": site["nome"],
                                  "url_externa": f"https://{site['dominio']}/", "completo": 1, "origem": ORIGEM},
                            rede=REDE)
            legenda = item["titulo"] + (f"\n\n{item['texto']}" if item["texto"] else "")
            cursor = con.execute(
                "INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, medido_em) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (rede, post_id) DO NOTHING",
                (REDE, item["link"] or item["id"], site["dominio"], item["link"] or None, noticias.TIPO,
                 item["publicado_em"], legenda, db.agora()))
            resumo["novas" if cursor.rowcount else "repetidas"] += 1
            vistas.add(_chave(item["titulo"]))
    return resumo


def coletar(con, aviso=print, baixar_=baixar, agora: datetime | None = None, dias: int = DIAS,
            paralelo: int = 6) -> Counter:
    """Lê o feed de cada site da base (um pedido por site) e grava as matérias de energia."""
    agora = agora or datetime.now(timezone.utc)
    municipios = mun.carregar(con)
    sites = [dict(l) for l in con.execute("SELECT * FROM sites WHERE feed IS NOT NULL ORDER BY dominio")]
    resumo = Counter()

    def ler(site):  # roda em outra thread: só rede
        try:
            return (ler_wp if site["formato"] == "wp_json" else ler_feed)(baixar_(site["feed"])), None
        except Exception as erro:
            return [], f"{type(erro).__name__}: {erro}"[:200]

    with ThreadPoolExecutor(paralelo) as executor:
        for site, (itens, erro) in zip(sites, executor.map(ler, sites)):
            if erro:
                resumo["sites_com_erro"] += 1
            else:
                resumo["sites_lidos"] += 1
                resumo.update(ingerir(con, site, itens, municipios, agora, dias))
            con.execute("UPDATE sites SET lido_em = ?, itens_ultimo = ?, erro = ? WHERE dominio = ?",
                        (db.agora(), len(itens), erro, site["dominio"]))
            con.commit()
    if resumo["novas"]:
        aviso(f"Sites: {resumo['novas']} matérias novas sobre energia de {resumo['sites_lidos']} sites")
    return resumo


def analisar_e_alertar(con, cliente, aviso=print) -> int:
    """A DeepSeek analisa as matérias pendentes (dos sites e do Google Notícias); a negativa vira alerta."""
    from .vigia import alertar

    posts = noticias.candidatos(con)
    if not posts:
        return 0
    mencoes.analisar(con, cliente, posts, aviso=lambda _: None)
    alertar(con, posts, aviso)
    return sum(1 for p in posts if (con.execute("SELECT sobre_energia FROM mencoes WHERE rede = ? AND post_id = ?",
                                                (p["rede"], p["post_id"])).fetchone() or [0])[0])


# --- Na vigilância ---------------------------------------------------------------------------

def _vez(con, termo: str, intervalo: timedelta, agora: datetime) -> bool:
    """Se a tarefa `termo` venceu, marca o início (numa transação imediata, para um trabalhador só pegar)."""
    if con.in_transaction:
        con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        ultima = perfis.ler_data((con.execute("SELECT executada_em FROM buscas WHERE fonte = 'sites' AND termo = ?",
                                              (termo,)).fetchone() or [None])[0])
        vez = ultima is None or agora - ultima >= intervalo
        if vez:
            con.execute("INSERT INTO buscas (fonte, termo, executada_em, n_resultados) VALUES ('sites', ?, ?, NULL) "
                        "ON CONFLICT (fonte, termo) DO UPDATE SET executada_em = excluded.executada_em",
                        (termo, agora.isoformat(timespec="seconds")))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return vez


def rodada(con, cliente, agora: datetime | None = None, aviso=print, baixar_=baixar) -> Counter:
    """A cada hora: os feeds dos sites; a cada 6 h, também o Google Notícias; depois a análise e os alertas. Sem
    leitura do Instagram."""
    agora = agora or datetime.now(timezone.utc)
    resumo = Counter()
    # Sem a base (python -m radar sites-descobrir), não há rodada, nem a do Google Notícias.
    if not con.execute("SELECT 1 FROM sites WHERE feed IS NOT NULL LIMIT 1").fetchone():
        return resumo
    if not _vez(con, "rodada", INTERVALO, agora):
        return resumo
    resumo.update(coletar(con, aviso, baixar_, agora))
    if _vez(con, "google_noticias", GOOGLE_A_CADA, agora):
        resumo.update({f"google_{k}": v for k, v in noticias.coletar(con, dias=3, aviso=lambda _: None).items()})
    if cliente is not None:
        resumo["mencoes"] += analisar_e_alertar(con, cliente, aviso)
    return resumo


def situacao(con) -> dict:
    linha = con.execute("SELECT COUNT(*), SUM(feed IS NOT NULL), MAX(lido_em) FROM sites").fetchone()
    tipos = dict(con.execute("SELECT tipo, COUNT(*) FROM sites WHERE feed IS NOT NULL GROUP BY tipo").fetchall())
    materias = con.execute("SELECT COUNT(*) FROM posts p JOIN contas c ON c.rede = p.rede AND c.username = p.username "
                           "WHERE p.rede = ? AND c.origem = ?", (REDE, ORIGEM)).fetchone()[0]
    return {"sites": linha[0], "com_feed": linha[1] or 0, "por_tipo": tipos, "ultima_leitura": linha[2],
            "materias_de_sites_novos": materias}
