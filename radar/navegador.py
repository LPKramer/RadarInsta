"""Coleta pelo navegador: o Edge, logado no Instagram, abre as páginas como uma pessoa faria.

Cada leitura é uma visita à página de um perfil (ou uma rolagem dela, à página de stories da conta, ou uma busca
na caixa de pesquisa). O radar
não chama endereços por conta própria: ele lê as respostas que o próprio site recebe para montar a página
(`/api/graphql`). Uma visita ao perfil traz os dados da conta, os 12 posts mais recentes e as contas parecidas;
cada rolagem traz mais 12 posts.

Não há custo por resultado. A classe `Navegador` tem a interface de `apify.Apify` (`rodar`) e devolve os itens no
formato dos atores da Apify, então enriquecer, monitorar e historico funcionam sem mudança. As visitas seguem um
ritmo lento e param no primeiro aviso do Instagram (429, checkpoint, pedido de login); o que já foi gravado fica, e
os comandos continuam de onde pararam. Comentários ainda não: a página do post só os carrega com mais interação.
Os stories vêm embutidos no HTML da página /stories/<conta>/, antes do botão "Ver story": o radar os lê dali, sem
abrir o visualizador.

O formato das respostas é interno do site e pode mudar; toda a leitura fica neste arquivo.
"""
import json
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlsplit, urlunsplit

from . import capas, config, perfis

BASE = "https://www.instagram.com"
ALFABETO_SHORTCODE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
POSTS_POR_PAGINA = 12  # a grade do perfil carrega 12 posts por vez
TIPOS_POST = {1: "Image", 2: "Video", 8: "Sidecar"}
# Chaves de `data` nas respostas do site.
CHAVE_POSTS = "xdt_api__v1__feed__user_timeline_graphql_connection"
CHAVE_PARECIDAS = "xdt_api__v1__discover__chaining"
CHAVE_BUSCA = "xdt_api__v1__fbsearch__topsearch_connection"
CHAVE_STORIES = "xdt_api__v1__feed__reels_media"
CHAVE_STORY_VISTO = "xdt_mark_story_reel_seen"
CHAVE_BUSCA_POSTS = "xdt_fbsearch__top_serp_graphql"  # a grade de posts da busca por palavra-chave
# Comentários do post: os ~15 primeiros vêm no HTML de /p/<código>/ e os seguintes, de 10 em 10, quando o painel de
# comentários rola; os dois em data.comments_media.comments_connection.edges[].node (XIGComment).
CHAVE_COMENTARIOS = "comments_connection"
ROLAR_COMENTARIOS = """() => {
  let n = 0;
  for (const el of document.querySelectorAll('div, section, ul')) {
    const s = getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 50) {
      el.scrollTop = el.scrollHeight; n++;
    }
  }
  return n;
}"""
# Respostas e endereços que querem dizer "pare agora": conta limitada, verificação pendente ou sessão vencida.
SINAIS_BLOQUEIO = ("wait a few minutes", "checkpoint", "challenge", "login_required", "feedback_required")
URL_SUSPENSA = "/accounts/suspended"
URLS_BLOQUEIO = ("/accounts/login", "/challenge", URL_SUSPENSA, "/checkpoint")
TEXTOS_INDISPONIVEL = ("isn't available", "não está disponível", "no está disponible")
ESPERA_PAGINA = 15  # segundos para a página de um perfil carregar os dados
ESPERA_ROLAGEM = 12
MAX_FALHAS_SEGUIDAS = 5
RE_BUSCA = re.compile("Pesquis|Search|Buscar", re.I)
PASTA_PERFIL = config.NAVEGADOR_DIR / "perfil"  # a conta "principal"; as outras ficam em perfil-<nome>
CONTA_PRINCIPAL = "principal"
RE_NOME_CONTA = re.compile(r"^[a-z0-9_-]{1,30}$")
CAMINHOS_EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def pasta_conta(conta: str = CONTA_PRINCIPAL) -> Path:
    """Perfil do Edge de uma conta do Instagram (cada conta tem o seu, com a sua sessão)."""
    if conta == CONTA_PRINCIPAL:
        return PASTA_PERFIL
    if not RE_NOME_CONTA.match(conta):
        raise ValueError(f"Nome de conta inválido: {conta!r} (use letras minúsculas, números, - ou _).")
    return config.NAVEGADOR_DIR / f"perfil-{conta}"


def arquivo_leituras(conta: str = CONTA_PRINCIPAL) -> Path:
    """O ritmo e os tetos valem por conta: cada uma tem o seu registro de leituras."""
    return config.NAVEGADOR_DIR / ("leituras.json" if conta == CONTA_PRINCIPAL else f"leituras-{conta}.json")


def contas_configuradas(pasta: Path | None = None) -> list[str]:
    """Contas com perfil do Edge criado (logadas ou não), a principal primeiro."""
    pasta = pasta or config.NAVEGADOR_DIR
    if not pasta.exists():
        return []
    nomes = [p.name for p in pasta.iterdir() if p.is_dir() and (p.name == "perfil" or p.name.startswith("perfil-"))]
    return sorted((CONTA_PRINCIPAL if n == "perfil" else n.removeprefix("perfil-") for n in nomes),
                  key=lambda n: (n != CONTA_PRINCIPAL, n))


def arquivo_redes(conta: str, pasta: Path | None = None) -> Path:
    return (pasta or config.NAVEGADOR_DIR) / f"redes-{conta}.txt"


def redes_da_conta(conta: str, pasta: Path | None = None) -> set[str]:
    """As redes em que a conta trabalha (redes.py). Sem o arquivo, só o Instagram: uma conta criada para o Threads
    pode ter o cookie do Instagram e, mesmo assim, não entra nos comandos do Instagram."""
    try:
        return set(arquivo_redes(conta, pasta).read_text(encoding="utf-8").split()) or {"instagram"}
    except FileNotFoundError:
        return {"instagram"}


def definir_redes(conta: str, redes: set[str], pasta: Path | None = None) -> None:
    caminho = arquivo_redes(conta, pasta)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text("\n".join(sorted(redes)) + "\n", encoding="utf-8")


def arquivo_desativada(conta: str, pasta: Path | None = None) -> Path:
    return (pasta or config.NAVEGADOR_DIR) / f"desativada-{conta}.txt"


def desativada(conta: str, pasta: Path | None = None) -> str | None:
    """O motivo, se a conta está fora do trabalho (suspensa pelo Instagram ou tirada à mão); None se está ativa."""
    try:
        return arquivo_desativada(conta, pasta).read_text(encoding="utf-8").strip() or "sem motivo"
    except FileNotFoundError:
        return None


def desativar(conta: str, motivo: str, pasta: Path | None = None) -> None:
    """Tira a conta da vigilância e dos comandos em paralelo, sem apagar o login. Volta com `ativar`."""
    pasta_conta(conta)  # confere o nome
    caminho = arquivo_desativada(conta, pasta)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(f"{motivo} (desde {datetime.now():%d/%m/%Y %H:%M})", encoding="utf-8")


def ativar(conta: str, pasta: Path | None = None) -> bool:
    caminho = arquivo_desativada(conta, pasta)
    existia = caminho.exists()
    caminho.unlink(missing_ok=True)
    return existia


class ColetaInterrompida(RuntimeError):
    """O Instagram pediu uma pausa, a janela foi fechada ou o teto de leituras chegou. O que já foi gravado fica."""


class TetoAtingido(ColetaInterrompida):
    """O teto diário de leituras da conta (RADAR_NAV_MAX_DIA) chegou. Não é aviso do Instagram: a vigilância espera
    as leituras mais velhas saírem da janela de 24 horas e continua."""

    def __init__(self, mensagem: str, espera: float):
        super().__init__(mensagem)
        self.espera = espera


class SemLogin(RuntimeError):
    pass


class ContaDesativada(SemLogin):
    """A conta foi suspensa pelo Instagram ou tirada do trabalho com `navegador-desativar`."""


# --- Conversão para o formato da Apify ------------------------------------------------------

def shortcode_para_pk(shortcode: str) -> str:
    """O shortcode da URL é o id numérico do post escrito em base 64."""
    pk = 0
    for letra in shortcode:
        pk = pk * 64 + ALFABETO_SHORTCODE.index(letra)
    return str(pk)


def _iso(timestamp) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(int(timestamp), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _instante(valor: str) -> datetime:
    """'2026-09-02' ou '2026-09-02T10:00:00.000Z' -> datetime com fuso."""
    data = perfis.ler_data(valor if "T" in valor else f"{valor}T00:00:00+00:00")
    return data if data.tzinfo else data.replace(tzinfo=timezone.utc)


RE_DURACAO_DASH = re.compile(r"^PT(?:([\d.]+)H)?(?:([\d.]+)M)?(?:([\d.]+)S)?$")


def _dash(manifesto: str | None) -> tuple[str | None, float | None]:
    """Do manifesto DASH que a grade traz em cada vídeo: o endereço da faixa só de áudio de menor taxa (uns 55 kbps,
    menos de 1 MB por minuto) e a duração em segundos."""
    import xml.etree.ElementTree as ET

    if not manifesto:
        return None, None
    try:
        raiz = ET.fromstring(manifesto)
    except ET.ParseError:
        return None, None
    tag = lambda el: el.tag.rsplit("}", 1)[-1]  # noqa: E731 (sem o namespace do DASH)
    achado = RE_DURACAO_DASH.match(raiz.get("mediaPresentationDuration") or "")
    duracao = (sum(float(v or 0) * f for v, f in zip(achado.groups(), (3600, 60, 1))) or None) if achado else None
    audios = []
    for conjunto in (el for el in raiz.iter() if tag(el) == "AdaptationSet"):
        for rep in (el for el in conjunto.iter() if tag(el) == "Representation"):
            tipo = rep.get("mimeType") or conjunto.get("mimeType") or conjunto.get("contentType") or ""
            base = next((el.text.strip() for el in rep if tag(el) == "BaseURL" and el.text), None)
            if tipo.startswith("audio") and base:
                audios.append((int(rep.get("bandwidth") or 0), base))
    return (min(audios)[1] if audios else None), duracao


def video_do_site(no: dict) -> dict:
    """Do vídeo da grade: a menor versão do vídeo, a faixa só de áudio (a que a transcrição das rádios baixa) e a
    duração. Os endereços são assinados e vencem em uns 2 dias. Vazio se não é vídeo."""
    if no.get("media_type") != 2:
        return {}
    versoes = [v for v in no.get("video_versions") or [] if v.get("url")]
    menor = min(versoes, key=lambda v: (v.get("width") or 0) * (v.get("height") or 0))["url"] if versoes else None
    audio, duracao = _dash(no.get("video_dash_manifest"))
    return {"videoUrl": menor, "audioUrl": audio, "videoDuration": no.get("video_duration") or duracao}


def post_do_site(no: dict) -> dict:
    """Post da grade do perfil (`edges[].node`) -> post no formato da Apify."""
    return video_do_site(no) | {
        "id": str(no.get("pk")),
        "shortCode": no.get("code"),
        "url": f"{BASE}/p/{no.get('code')}/",
        "type": TIPOS_POST.get(no.get("media_type"), "Image"),
        "caption": (no.get("caption") or {}).get("text"),
        # -1 é como a Apify marca curtidas ocultas.
        "likesCount": -1 if no.get("like_and_view_counts_disabled") else no.get("like_count"),
        "commentsCount": no.get("comment_count"),
        "videoViewCount": no.get("play_count") or no.get("view_count"),
        "timestamp": _iso(no.get("taken_at")),
        "locationName": (no.get("location") or {}).get("name"),
        "isPinned": bool(no.get("timeline_pinned_user_ids")),
        "ownerUsername": ((no.get("user") or {}).get("username") or "").lower() or None,
        "ownerFullName": (no.get("user") or {}).get("full_name"),  # a busca de posts traz o autor resumido
        "ownerIsPrivate": bool((no.get("user") or {}).get("is_private")),
        "displayUrl": capas.melhor_imagem(no),  # mesmo nome da Apify; vira posts.capa
    }


def medias_da_busca(conexao: dict) -> list[dict]:
    """Posts da busca por palavra-chave (`edges[].node.items[]`), sem repetir."""
    vistos, medias = set(), []
    for aresta in conexao.get("edges") or []:
        for media in ((aresta or {}).get("node") or {}).get("items") or []:
            if isinstance(media, dict) and media.get("code") and media["code"] not in vistos:
                vistos.add(media["code"])
                medias.append(media)
    return medias


def item_de_perfil(usuario: dict, nos: list[dict], parecidas: list[dict]) -> dict:
    """Dados da página do perfil -> item do profile scraper da Apify."""
    username = (usuario.get("username") or "").lower()
    posts = [post_do_site(no) for no in nos]
    for post in posts:
        post["ownerUsername"] = post["ownerUsername"] or username
    return {
        "id": str(usuario.get("pk") or usuario.get("id")),
        "username": username,
        "fullName": usuario.get("full_name"),
        "biography": usuario.get("biography"),
        "followersCount": usuario.get("follower_count"),
        "followsCount": usuario.get("following_count"),
        "postsCount": usuario.get("media_count"),
        "private": usuario.get("is_private"),
        "verified": usuario.get("is_verified"),
        "isBusinessAccount": usuario.get("is_business"),
        "businessCategoryName": usuario.get("category") or None,
        "externalUrl": usuario.get("external_url"),
        "profilePicUrl": capas.foto_do_usuario(usuario),
        "latestPosts": posts,
        "relatedProfiles": [{"username": u.get("username"), "full_name": u.get("full_name"),
                             "is_private": u.get("is_private")} for u in parecidas if u.get("username")],
    }


def story_do_site(item: dict, dono: str) -> dict:
    """Story (`reels_media[].items[]`) -> o que o radar guarda: endereços da mídia e o texto que vem pronto."""
    imagens = (item.get("image_versions2") or {}).get("candidates") or []
    maior = max(imagens, key=lambda c: c.get("width") or 0, default={})
    videos = item.get("video_versions") or []
    marcadas = [(((s.get("bloks_sticker") or {}).get("sticker_data") or {}).get("ig_mention") or {})
                for s in item.get("story_bloks_stickers") or []]
    marcadas += [m.get("user") or {} for m in item.get("reel_mentions") or []]
    locais = [(l.get("location") or {}).get("name") for l in item.get("story_locations") or []]
    return {
        "id": str(item.get("pk")),
        "timestamp": _iso(item.get("taken_at")),
        "url": f"{BASE}/stories/{dono}/{item.get('pk')}/",
        "imagem": maior.get("url"),
        "video": videos[0].get("url") if videos else None,
        "textoAlt": item.get("accessibility_caption") or "",
        "links": [link_real((s.get("story_link") or {}).get("url")) for s in item.get("story_link_stickers") or []
                  if (s.get("story_link") or {}).get("url")],
        # O nome de exibição vai junto: "Energisa Tocantins" passa no filtro de palavras, o @ grudado nem sempre.
        "mencoes": [f"{m['username']} ({m['full_name']})" if m.get("full_name") else m["username"]
                    for m in marcadas if m.get("username")],
        "hashtags": [(h.get("hashtag") or {}).get("name") for h in item.get("story_hashtags") or []
                     if (h.get("hashtag") or {}).get("name")],
        "locationName": next((l for l in locais if l), None),
    }


def link_real(url: str) -> str:
    """O Instagram embrulha os links em l.instagram.com/?u=<endereço>; devolve o endereço da matéria, sem o fbclid."""
    partes = urlsplit(url)
    if partes.netloc.endswith("l.instagram.com"):
        url = parse_qs(partes.query).get("u", [url])[0]
    partes = urlsplit(url)
    consulta = [(k, v) for k, v in parse_qsl(partes.query) if k != "fbclid"]
    return urlunsplit(partes._replace(query=urlencode(consulta)))


def stories_do_html(html: str, username: str) -> list[dict]:
    """Os stories da conta, nos blocos JSON que o site embute no HTML da página /stories/<conta>/."""
    for bloco in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', html, re.S):
        if CHAVE_STORIES not in bloco:
            continue
        try:
            dados = json.loads(bloco)
        except ValueError:
            continue
        for reel in (_procurar(dados, CHAVE_STORIES) or {}).get("reels_media") or []:
            if ((reel.get("user") or {}).get("username") or "").lower() == username.lower():
                return reel.get("items") or []
    return []


def comentarios_da_conexao(dados) -> tuple[list[dict], bool]:
    """Os comentários de toda `comments_connection` em `dados` (só ali: a legenda do post tem o mesmo formato de um
    comentário) e se o site tem mais páginas."""
    nos, tem_mais = [], False

    def andar(valor):
        nonlocal tem_mais
        if isinstance(valor, dict):
            conexao = valor.get(CHAVE_COMENTARIOS)
            if isinstance(conexao, dict):
                for aresta in conexao.get("edges") or []:
                    no = (aresta or {}).get("node") if isinstance(aresta, dict) else None
                    if isinstance(no, dict) and no.get("text") is not None and (no.get("pk") or no.get("id")):
                        nos.append(no)
                tem_mais = tem_mais or bool((conexao.get("page_info") or {}).get("has_next_page"))
            for chave, item in valor.items():
                if chave != CHAVE_COMENTARIOS:
                    andar(item)
        elif isinstance(valor, list):
            for item in valor:
                andar(item)

    andar(dados)
    return nos, tem_mais


def comentarios_do_html(html: str) -> tuple[list[dict], bool]:
    """Os comentários que o site embute no HTML da página do post."""
    nos, tem_mais = [], False
    for bloco in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', html, re.S):
        if CHAVE_COMENTARIOS not in bloco:
            continue
        try:
            dados = json.loads(bloco)
        except ValueError:
            continue
        achados, mais = comentarios_da_conexao(dados)
        nos += achados
        tem_mais = tem_mais or mais
    return nos, tem_mais


def comentario_do_site(no: dict, url_post: str) -> dict:
    """Comentário -> item no formato do comment scraper da Apify. Quem comentou fica de fora: o radar só guarda o
    texto, as curtidas e a data."""
    return {"postUrl": url_post, "id": str(no.get("pk") or no.get("id")), "text": no.get("text"),
            "likesCount": no.get("comment_like_count"), "timestamp": _iso(no.get("created_at")),
            "repliesCount": no.get("child_comment_count")}


def _procurar(valor, chave: str):
    """Primeiro valor sob `chave`, em qualquer nível."""
    if isinstance(valor, dict):
        if chave in valor:
            return valor[chave]
        valor = list(valor.values())
    if isinstance(valor, list):
        for item in valor:
            achado = _procurar(item, chave)
            if achado is not None:
                return achado
    return None


def usuario_resumido(usuario: dict) -> dict:
    """Usuário da busca ou das contas parecidas -> o mínimo que a descoberta usa."""
    usuario = usuario.get("user") or usuario
    return {
        "username": (usuario.get("username") or "").lower(),
        "nome": usuario.get("full_name"),
        "id": str(usuario.get("pk") or usuario.get("id") or "") or None,
        "privada": bool(usuario.get("is_private")),
        "verificada": bool(usuario.get("is_verified")),
    }


def checar_resposta(status: int, corpo) -> None:
    """Para tudo se a resposta do site for um aviso de bloqueio."""
    mensagem = str(corpo.get("message") or "") if isinstance(corpo, dict) else ""
    exige_login = isinstance(corpo, dict) and corpo.get("require_login")
    if status == 429 or exige_login or any(s in mensagem.lower() for s in SINAIS_BLOQUEIO):
        raise ColetaInterrompida(
            f"O Instagram pediu uma pausa (HTTP {status}: {mensagem[:120] or 'sem mensagem'}). O que já foi coletado "
            "está gravado. Espere algumas horas, abra o Instagram no navegador para ver se há algum aviso na conta "
            "e rode o comando de novo: ele continua de onde parou.")


def ler_visita(capturas: list[tuple[int, object]], username: str, visita: dict | None = None) -> dict:
    """Junta, das respostas capturadas numa visita, o perfil, os posts e as contas parecidas."""
    visita = visita or {"user": None, "posts": [], "tem_mais": False, "parecidas": [], "status": None}
    for status, corpo in capturas:
        checar_resposta(status, corpo)
        dados = (corpo.get("data") if isinstance(corpo, dict) else None) or {}
        usuario = dados.get("user")
        if isinstance(usuario, dict) and "follower_count" in usuario \
                and (usuario.get("username") or "").lower() == username.lower():
            visita["user"] = usuario
        conexao = dados.get(CHAVE_POSTS)
        if isinstance(conexao, dict):
            visita["posts"] += [e.get("node") or {} for e in conexao.get("edges") or []]
            visita["tem_mais"] = bool((conexao.get("page_info") or {}).get("has_next_page"))
        parecidas = dados.get(CHAVE_PARECIDAS)
        if isinstance(parecidas, dict):
            visita["parecidas"] = parecidas.get("users") or []
    return visita


# --- Ritmo ----------------------------------------------------------------------------------

class Ritmo:
    """Pausa aleatória entre as leituras, pausa longa de tempos em tempos e tetos por hora e por dia. As leituras
    das últimas 24 h ficam num arquivo, para o teto diário valer entre uma execução e outra."""

    def __init__(self, arquivo: Path | None = None, dormir=time.sleep, relogio=time.time, uniforme: bool = False,
                 por_hora: float | None = None):
        self.arquivo = arquivo
        self.dormir, self.relogio = dormir, relogio
        self.leituras = self._carregar()
        self.feitas = 0
        # Uniforme (vigilância contínua): as leituras se espalham pelo dia, em vez de gastar o teto da hora em
        # minutos e ficar parado até a hora virar. Com `por_hora` (a volta), o ritmo uniforme é fixo.
        self.uniforme = uniforme
        self.por_hora = por_hora

    def intervalo_uniforme(self) -> float:
        """Segundos entre leituras. Com `por_hora`, é o ritmo fixo pedido, sem passar do teto por hora. Sem ele, é o
        ritmo que gasta o que resta do teto diário nas próximas 12 h, sem passar do teto por hora e sem cair abaixo
        do ritmo de um dia inteiro."""
        if self.por_hora:
            return 3600 / min(config.NAV_MAX_HORA, self.por_hora)
        restante = max(config.NAV_MAX_DIA - len(self.leituras), 0)
        por_hora = min(config.NAV_MAX_HORA, max(config.NAV_MAX_DIA / 24, restante / 12))
        return 3600 / por_hora

    def _carregar(self) -> list[float]:
        try:
            return [float(t) for t in json.loads(self.arquivo.read_text(encoding="utf-8"))]
        except (AttributeError, OSError, ValueError, TypeError):
            return []

    def espera_do_teto(self) -> float:
        """Segundos até caber mais uma leitura no teto diário (0 se cabe agora)."""
        agora = self.relogio()
        self.leituras = [t for t in self.leituras if agora - t < 86400]
        if len(self.leituras) < config.NAV_MAX_DIA:
            return 0.0
        return max(self.leituras[-config.NAV_MAX_DIA] + 86400 - agora, 1.0)

    def esperar(self) -> None:
        espera = self.espera_do_teto()
        if espera:
            raise TetoAtingido(f"Teto de {config.NAV_MAX_DIA} leituras em 24 horas atingido "
                               "(RADAR_NAV_MAX_DIA). Rode de novo amanhã: o comando continua de onde parou.", espera)
        agora = self.relogio()
        na_hora = [t for t in self.leituras if agora - t < 3600]
        if len(na_hora) >= config.NAV_MAX_HORA:
            self.dormir(3600 - (agora - na_hora[0]) + random.uniform(5, 30))
        elif self.feitas and self.feitas % config.NAV_LONGA_A_CADA == 0:
            self.dormir(random.uniform(*config.NAV_PAUSA_LONGA))
        elif self.feitas and self.uniforme:
            desde_a_ultima = agora - self.leituras[-1] if self.leituras else 0
            alvo = self.intervalo_uniforme() * random.uniform(0.7, 1.3)
            self.dormir(max(random.uniform(*config.NAV_PAUSA), alvo - desde_a_ultima))
        elif self.feitas:
            self.dormir(random.uniform(*config.NAV_PAUSA))
        self.leituras.append(self.relogio())
        self.feitas += 1
        if self.arquivo:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            self.arquivo.write_text(json.dumps(self.leituras), encoding="utf-8")


# --- Coletor --------------------------------------------------------------------------------

class Navegador:
    """Mesma interface de `apify.Apify`, com as leituras feitas por visitas às páginas do Instagram.

    `cliente` é a página (ou um substituto nos testes) com `visitar_perfil(username)`, `rolar()` e
    `pesquisar(termo)`."""

    lote_maximo = 10  # lotes pequenos: se o Instagram interromper, perde-se no máximo um lote

    def __init__(self, cliente, ritmo: Ritmo | None = None, raw_dir: Path = config.RAW_DIR):
        self.cliente = cliente
        self.ritmo = ritmo or Ritmo(config.NAVEGADOR_DIR / "leituras.json")
        self.raw_dir = raw_dir
        self.gasto_usd = 0.0
        self.leituras = 0
        self.bloqueio: str | None = None
        self._falhas = 0
        self._ultimo_story: dict[str, int] = {}  # latest_reel_media de cada perfil visitado
        self._ultima_visita: dict[str, dict] = {}  # o que a última visita a cada perfil trouxe

    def espera_do_teto(self) -> float:
        return self.ritmo.espera_do_teto()

    def _ler(self, acao):
        """Uma leitura com pausa antes; o primeiro aviso de bloqueio para esta e todas as próximas."""
        if self.bloqueio:
            raise ColetaInterrompida(self.bloqueio)
        self.ritmo.esperar()
        try:
            resultado = acao()
        except ColetaInterrompida as erro:
            self.bloqueio = str(erro)
            raise
        self.leituras += 1
        return resultado

    def _visitar(self, username: str, parecidas: bool = True) -> dict:
        visita = self._ler(lambda: self.cliente.visitar_perfil(username, parecidas))
        if visita.get("user"):
            self._ultimo_story[username.lower()] = visita["user"].get("latest_reel_media") or 0
            self._ultima_visita[username.lower()] = visita
        if visita.get("user") is None and visita.get("status") != "nao_encontrado":
            self._falhas += 1
            if self._falhas >= MAX_FALHAS_SEGUIDAS:
                self.bloqueio = (f"{self._falhas} páginas de perfil seguidas sem dados. O site pode ter mudado ou "
                                 "a conta foi limitada; o que já foi coletado está gravado.")
                raise ColetaInterrompida(self.bloqueio)
        else:
            self._falhas = 0
        return visita

    def rodar(self, ator: str, entrada: dict, rotulo: str, max_usd: float | None = None) -> tuple[str, list[dict]]:
        if ator == config.ATOR_PERFIL:
            itens = [self.perfil(u) for u in entrada["usernames"]]
        elif ator == config.ATOR_POSTS:
            por_conta = entrada.get("desde_por_conta") or {}
            itens = [p for u in entrada["username"]
                     for p in self.posts(u, por_conta.get(u) or entrada["onlyPostsNewerThan"],
                                         entrada.get("resultsLimit"), entrada.get("atualizar_desde"))]
        elif ator == config.ATOR_COMENTARIOS:
            limite = entrada.get("resultsLimit") or config.COMENTARIOS_POR_POST
            itens = [c for url in entrada["directUrls"] for c in self.comentarios(url, limite)]
        else:
            raise ValueError(f"O coletor pelo navegador ainda não substitui o ator {ator}. Use --via apify.")
        return self._guardar(ator, entrada, rotulo, itens), itens

    def perfil(self, username: str) -> dict:
        visita = self._visitar(username)
        if not visita["user"]:
            return {"username": username, "error": "not_found"}
        return item_de_perfil(visita["user"], visita["posts"], visita["parecidas"])

    def posts(self, username: str, desde: str, limite: int | None = None,
              atualizar_desde: str | None = None) -> list[dict]:
        """Posts publicados a partir de `desde` (data ou instante ISO), do mais novo ao mais antigo, rolando a grade
        do perfil. Posts fixados antigos ficam de fora sem interromper a leitura.

        Com `atualizar_desde`, os posts da primeira página (que vem de graça com a visita) publicados a partir dessa
        data também voltam, para atualizar curtidas e comentários; rolar, só por post novo."""
        visita = self._visitar(username, parecidas=False)  # o monitoramento não usa as contas parecidas
        nos, tem_mais = visita["posts"], visita["tem_mais"]
        corte = _instante(desde)
        corte_gratis = min(corte, _instante(atualizar_desde)) if atualizar_desde else corte
        primeira_pagina = True
        encontrados = []
        while True:
            passou_do_corte = False
            for no in nos:
                post = post_do_site(no)
                post["ownerUsername"] = post["ownerUsername"] or username
                quando = perfis.ler_data(post["timestamp"])
                if quando is None or quando < corte:
                    passou_do_corte = passou_do_corte or not post["isPinned"]
                    if primeira_pagina and quando is not None and quando >= corte_gratis:
                        encontrados.append(post)
                    continue
                encontrados.append(post)
                if limite and len(encontrados) >= limite:
                    return encontrados
            if passou_do_corte or not tem_mais:
                return encontrados
            primeira_pagina = False
            nos, tem_mais = self._ler(self.cliente.rolar)
            if not nos:
                return encontrados

    def perfil_visto(self, username: str) -> dict | None:
        """O perfil (no formato da Apify) da última visita feita nesta sessão, sem ler de novo."""
        visita = self._ultima_visita.get(username.lower())
        return item_de_perfil(visita["user"], visita["posts"], visita.get("parecidas") or []) if visita else None

    def foto_vista(self, username: str) -> str | None:
        """Endereço da foto do perfil trazida pela última visita, sem ler de novo (o endereço expira em ~4 dias)."""
        visita = self._ultima_visita.get(username.lower())
        return capas.foto_do_usuario(visita["user"]) if visita and visita.get("user") else None

    def story_novo(self, username: str, ultimo_gravado: datetime | None) -> bool:
        """Se a última visita ao perfil mostrou um story das últimas 24 horas mais novo que o último já gravado."""
        quando = self._ultimo_story.get(username.lower())
        if not quando:
            return False
        quando = datetime.fromtimestamp(quando, timezone.utc)
        if datetime.now(timezone.utc) - quando > timedelta(hours=24):
            return False
        return ultimo_gravado is None or quando > ultimo_gravado

    def comentarios(self, url: str, maximo: int) -> list[dict]:
        """Comentários do post, sem as respostas, até `maximo`: os do HTML e, rolando o painel, as páginas seguintes.
        Cada página é uma leitura."""
        codigo = perfis.shortcode_de_url(url)
        if not codigo:
            return []
        nos, tem_mais = self._ler(lambda: self.cliente.abrir_comentarios(codigo))
        vistos: dict[str, dict] = {}
        for no in nos:
            vistos.setdefault(str(no.get("pk") or no.get("id")), no)
        while tem_mais and len(vistos) < maximo:
            novos, tem_mais = self._ler(lambda: self.cliente.mais_comentarios())
            antes = len(vistos)
            for no in novos:
                vistos.setdefault(str(no.get("pk") or no.get("id")), no)
            if len(vistos) == antes:
                break
        return [comentario_do_site(no, url) for no in list(vistos.values())[:maximo]]

    def stories(self, username: str) -> list[dict]:
        """Stories ativos da conta."""
        itens = self._ler(lambda: self.cliente.ver_stories(username))
        stories = [story_do_site(item, username) for item in itens]
        self._guardar("navegador/stories", {"username": username}, "stories", stories)
        return stories

    def baixar(self, url: str, destino: Path) -> bool:
        """Baixa a imagem ou o vídeo de um story. Não conta como leitura: é o que o navegador faz ao mostrar a página."""
        if self.bloqueio:
            raise ColetaInterrompida(self.bloqueio)
        return self.cliente.baixar(url, destino)

    def pesquisar(self, termo: str) -> list[dict]:
        """Contas que a caixa de busca do site mostra para o termo."""
        usuarios = [usuario_resumido(u) for u in self._ler(lambda: self.cliente.pesquisar(termo))]
        self._guardar("navegador/pesquisa", {"termo": termo}, "pesquisa", usuarios)
        return [u for u in usuarios if u["username"]]

    def buscar_posts(self, termo: str) -> list[dict]:
        """Posts que a busca por palavra-chave do site mostra para o termo (uma leitura, ~24 posts de qualquer
        conta), no formato da Apify, com o `ownerUsername`."""
        itens = [post_do_site(m) for m in self._ler(lambda: self.cliente.buscar_posts(termo))]
        self._guardar("navegador/busca_posts", {"termo": termo}, "busca_posts", itens)
        return [i for i in itens if i["ownerUsername"]]

    def _guardar(self, ator: str, entrada: dict, rotulo: str, itens: list) -> str:
        """Cópia bruta no mesmo formato da Apify, para o importar-brutos."""
        run_id = f"nav-{uuid.uuid4().hex[:12]}"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
        conteudo = {"ator": ator, "rotulo": rotulo, "run_id": run_id, "usd": 0, "via": "navegador",
                    "entrada": entrada, "itens": itens}
        (self.raw_dir / f"{carimbo}_{rotulo}_{run_id}.json").write_text(
            json.dumps(conteudo, ensure_ascii=False, default=str), encoding="utf-8")
        return run_id


# --- Edge pelo Playwright -------------------------------------------------------------------

class PaginaInstagram:
    """O Edge instalado, controlado pelo Playwright, com o perfil do navegador (e a sessão do Instagram) em
    data/navegador/perfil. Abre as páginas e lê as respostas que o site recebe."""

    def __init__(self, visivel: bool = True, pasta: Path = PASTA_PERFIL):
        self.visivel = visivel
        self.pasta = pasta
        self.capturas: list[tuple[int, object]] = []
        self.stories_marcados_como_vistos = 0

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self.pasta.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self.contexto = self._playwright.chromium.launch_persistent_context(
            str(self.pasta), channel="msedge", headless=not self.visivel, locale="pt-BR",
            viewport={"width": 1280, "height": 900})
        self.pagina = self.contexto.pages[0] if self.contexto.pages else self.contexto.new_page()
        self.pagina.on("response", self._registrar)
        self.pagina.goto(BASE + "/", wait_until="domcontentloaded")
        return self

    def __exit__(self, *_):
        try:
            self.contexto.close()
        except Exception:  # a janela já foi fechada
            pass
        finally:
            self._playwright.stop()

    def logado(self) -> bool:
        return any(c["name"] == "sessionid" and c.get("value") for c in self.contexto.cookies(BASE))

    def _registrar(self, resposta) -> None:
        if "/graphql" not in resposta.url:
            return
        try:
            self.capturas.append((resposta.status, resposta.json()))
        except Exception:
            self.capturas.append((resposta.status, None))

    def _esperar(self, segundos: float) -> None:
        """Espera deixando o site trabalhar; para se a janela fechou ou o Instagram levou a outra página."""
        try:
            self.pagina.wait_for_timeout(segundos * 1000)
            url = self.pagina.url
        except Exception as erro:
            raise ColetaInterrompida(f"O navegador parou de responder ({type(erro).__name__}). O que já foi "
                                     "coletado está gravado; rode o comando de novo.") from erro
        if any(trecho in url for trecho in URLS_BLOQUEIO):
            raise ColetaInterrompida(f"O Instagram levou o navegador para {url}. Resolva o aviso na janela do Edge "
                                     "(ou espere algumas horas) e rode o comando de novo; ele continua de onde parou.")

    def _ir(self, url: str) -> None:
        try:
            self.pagina.goto(url, wait_until="domcontentloaded")
        except Exception as erro:
            raise ColetaInterrompida(f"Não consegui abrir {url} ({type(erro).__name__}). O que já foi coletado está "
                                     "gravado; rode o comando de novo.") from erro

    def _indisponivel(self) -> bool:
        try:
            texto = self.pagina.evaluate("document.body ? document.body.innerText.slice(0, 3000) : ''")
        except Exception:
            return False
        return any(t in texto for t in TEXTOS_INDISPONIVEL)

    def visitar_perfil(self, username: str, parecidas: bool = True) -> dict:
        """Abre o perfil e espera os dados. Com `parecidas`, espera também as contas parecidas (só a descoberta usa;
        o monitoramento economiza uns 3 segundos por visita)."""
        self.capturas = []
        self._ir(f"{BASE}/{username}/")
        visita = None
        lidas = 0
        inicio = time.monotonic()
        extra = None
        while time.monotonic() - inicio < ESPERA_PAGINA:
            self._esperar(0.5)
            visita = ler_visita(self.capturas[lidas:], username, visita)
            lidas = len(self.capturas)
            usuario = visita["user"]
            completo = usuario and (visita["posts"] or usuario.get("is_private") or not usuario.get("media_count"))
            if completo:
                if not parecidas:
                    break
                extra = extra or time.monotonic() + 3  # um pouco mais, para as contas parecidas chegarem
                if visita["parecidas"] or time.monotonic() > extra:
                    break
            elif usuario is None and time.monotonic() - inicio > 6 and self._indisponivel():
                visita["status"] = "nao_encontrado"
                break
        visita = visita or ler_visita([], username)
        if visita["user"] is None and visita["status"] is None:
            visita["status"] = "sem_dados"
        return visita

    def rolar(self) -> tuple[list[dict], bool]:
        """Rola a grade do perfil até a próxima leva de posts chegar."""
        self.capturas = []
        lidas = 0
        inicio = time.monotonic()
        while time.monotonic() - inicio < ESPERA_ROLAGEM:
            try:
                self.pagina.mouse.wheel(0, 6000)
            except Exception as erro:
                raise ColetaInterrompida(f"O navegador parou de responder ({type(erro).__name__}).") from erro
            self._esperar(1.5)
            visita = ler_visita(self.capturas[lidas:], "")
            lidas = len(self.capturas)
            if visita["posts"]:
                return visita["posts"], visita["tem_mais"]
        return [], False

    def abrir_comentarios(self, codigo: str) -> tuple[list[dict], bool]:
        """Abre /p/<código>/ e lê os comentários embutidos no HTML (os ~15 primeiros)."""
        self.capturas = []
        self._ir(f"{BASE}/p/{codigo}/")
        self._esperar(3)
        try:
            html = self.pagina.content()
        except Exception as erro:
            raise ColetaInterrompida(f"O navegador parou de responder ({type(erro).__name__}).") from erro
        for status, corpo in self.capturas:
            checar_resposta(status, corpo)
        return comentarios_do_html(html)

    def mais_comentarios(self) -> tuple[list[dict], bool]:
        """Rola o painel de comentários até a próxima leva chegar."""
        self.capturas = []
        inicio = time.monotonic()
        while time.monotonic() - inicio < ESPERA_ROLAGEM:
            try:
                self.pagina.evaluate(ROLAR_COMENTARIOS)
            except Exception as erro:
                raise ColetaInterrompida(f"O navegador parou de responder ({type(erro).__name__}).") from erro
            self._esperar(1.5)
            nos, tem_mais = [], False
            for status, corpo in self.capturas:
                checar_resposta(status, corpo)
                achados, mais = comentarios_da_conexao(corpo)
                nos += achados
                tem_mais = tem_mais or mais
            if nos:
                return nos, tem_mais
        return [], False

    def ver_stories(self, username: str) -> list[dict]:
        """Abre /stories/<conta>/ e lê os stories embutidos no HTML, sem clicar em "Ver story"."""
        self.capturas = []
        self._ir(f"{BASE}/stories/{username}/")
        self._esperar(3)
        try:
            html = self.pagina.content()
        except Exception as erro:
            raise ColetaInterrompida(f"O navegador parou de responder ({type(erro).__name__}).") from erro
        for status, corpo in self.capturas:
            checar_resposta(status, corpo)
            if isinstance(corpo, dict) and CHAVE_STORY_VISTO in (corpo.get("data") or {}):
                self.stories_marcados_como_vistos += 1
        return stories_do_html(html, username)

    def baixar(self, url: str, destino: Path) -> bool:
        try:
            resposta = self.contexto.request.get(url, timeout=30000)
        except Exception:
            return False
        if not resposta.ok:
            return False
        Path(destino).write_bytes(resposta.body())
        return True

    def buscar_posts(self, termo: str) -> list[dict]:
        """Abre a busca por palavra-chave do site (a grade de posts) e lê os posts da primeira página."""
        self.capturas = []
        self._ir(f"{BASE}/explore/search/keyword/?q={quote(termo)}")
        inicio = time.monotonic()
        while time.monotonic() - inicio < ESPERA_PAGINA:
            self._esperar(1)
            for status, corpo in self.capturas:
                checar_resposta(status, corpo)
                conexao = ((corpo.get("data") if isinstance(corpo, dict) else None) or {}).get(CHAVE_BUSCA_POSTS)
                if isinstance(conexao, dict):
                    return medias_da_busca(conexao)
        return []

    def pesquisar(self, termo: str) -> list[dict]:
        """Digita o termo na caixa de busca do site e lê as contas sugeridas."""
        self.capturas = []
        self._ir(BASE + "/")
        self._esperar(3)
        try:
            self.pagina.get_by_role("link", name=RE_BUSCA).first.click(timeout=10000)
            self._esperar(1.5)
            self.pagina.get_by_placeholder(RE_BUSCA).first.fill(termo, timeout=10000)
        except ColetaInterrompida:
            raise
        except Exception as erro:
            raise ColetaInterrompida(f"Não achei a caixa de busca do Instagram ({type(erro).__name__}). O site pode "
                                     "ter mudado.") from erro
        inicio = time.monotonic()
        while time.monotonic() - inicio < ESPERA_ROLAGEM:
            self._esperar(1)
            for status, corpo in self.capturas:
                checar_resposta(status, corpo)
                conexao = ((corpo.get("data") if isinstance(corpo, dict) else None) or {}).get(CHAVE_BUSCA)
                if isinstance(conexao, dict) and conexao.get("users") is not None:
                    return conexao["users"]
        return []


@contextmanager
def abrir(visivel: bool = config.NAV_VISIVEL, conta: str = CONTA_PRINCIPAL, dormir=time.sleep,
          uniforme: bool = False, por_hora: float | None = None):
    """Navegador logado na conta pedida, pronto para coletar. `dormir` faz as pausas do ritmo (a vigilância passa
    uma que acorda quando pedem para parar); `uniforme` espalha as leituras pelo dia ou, com `por_hora` (a volta), as
    faz nesse ritmo fixo. Uma conta que o Instagram levar para a página de suspensão fica desativada, para não voltar
    ao trabalho sozinha."""
    motivo = desativada(conta)
    if motivo:
        raise ContaDesativada(f"A conta {conta!r} está desativada: {motivo}. Para voltar: "
                              f"python -m radar navegador-ativar {conta}")
    with PaginaInstagram(visivel, pasta_conta(conta)) as pagina:
        if not pagina.logado():
            raise SemLogin(f"A conta {conta!r} não está logada no Instagram. Rode: "
                           f"python -m radar navegador-login --conta {conta}")
        try:
            yield Navegador(pagina, Ritmo(arquivo_leituras(conta), dormir=dormir, uniforme=uniforme,
                                          por_hora=por_hora))
        except ColetaInterrompida as erro:
            if URL_SUSPENSA in str(erro):
                desativar(conta, "suspensa pelo Instagram")
            raise


# --- Login ----------------------------------------------------------------------------------

def _edge() -> str:
    for caminho in CAMINHOS_EDGE:
        if Path(caminho).exists():
            return caminho
    raise RuntimeError("Não encontrei o Microsoft Edge instalado.")


def tem_sessao(pasta: Path = PASTA_PERFIL, rede: str = "instagram") -> bool | None:
    """Se o perfil do navegador guarda a sessão da rede (no Instagram, o cookie sessionid; ver redes.py). Lê só o nome
    do cookie, numa cópia do arquivo; o valor fica criptografado. None se o Edge está com o arquivo aberto."""
    from .redes import REDES

    r = REDES[rede]
    origem = pasta / "Default" / "Network" / "Cookies"
    if not origem.exists():
        return False
    copia = Path(tempfile.gettempdir()) / f"radar_cookies_{uuid.uuid4().hex[:8]}.db"
    try:
        shutil.copyfile(origem, copia)
    except OSError:
        return None
    try:
        con = sqlite3.connect(copia)
        try:
            hosts = " OR ".join("host_key LIKE ?" for _ in r.dominios)
            return con.execute(f"SELECT 1 FROM cookies WHERE ({hosts}) AND name = ?",
                               (*[f"%{d}" for d in r.dominios], r.cookie)).fetchone() is not None
        finally:
            con.close()
    finally:
        copia.unlink(missing_ok=True)


def entrar(aviso=print, espera_max: int = 3600, conta: str = CONTA_PRINCIPAL, rede: str = "instagram") -> bool:
    """Abre o Edge comum, sem automação, com o perfil da conta na página de login da rede, e espera a janela ser
    fechada. Sem automação, a rede não estranha o login; a senha fica só no navegador. Uma conta nova criada para o
    Threads ou o X fica só naquela rede (redes-<conta>.txt); uma conta que já existia ganha a rede a mais."""
    from .redes import REDES

    r = REDES[rede]
    pasta = pasta_conta(conta)
    nova = not pasta.exists()

    def anotar():
        if rede != "instagram" or arquivo_redes(conta).exists():
            definir_redes(conta, {rede} if nova else redes_da_conta(conta) | {rede})

    if tem_sessao(pasta, rede):
        anotar()
        aviso(f"A conta {conta!r} já está logada no {r.titulo}.")
        return True
    pasta.mkdir(parents=True, exist_ok=True)
    aviso(f"Entre no {r.titulo} na janela do Edge que abriu e feche a janela quando terminar. "
          f"Espero até {espera_max // 60} minutos...")
    if not _edge_comum(pasta, r.login, espera_max, aviso):
        return False
    if tem_sessao(pasta, rede):
        anotar()
        aviso(f"Login feito. A sessão da conta {conta!r} no {r.titulo} fica guardada em {pasta}.")
        return True
    return False


def _edge_comum(pasta: Path, url: str, espera_max: int, aviso) -> bool:
    """Abre o Edge sem automação no perfil e espera a janela fechar. False se o tempo esgotou."""
    processo = subprocess.Popen([_edge(), f"--user-data-dir={pasta}", "--no-first-run",
                                 "--no-default-browser-check", "--disable-background-mode", url])
    fim = time.monotonic() + espera_max
    while processo.poll() is None and time.monotonic() < fim:
        time.sleep(3)
    if processo.poll() is None:
        aviso("Tempo esgotado. Feche a janela do Edge e rode o comando de novo.")
        return False
    return True


def abrir_edge(conta: str, aviso=print, espera_max: int = 3600) -> bool:
    """Abre o Edge comum da conta na rede dela (o Instagram, se a conta é de lá), para resolver um aviso, recorrer de
    uma suspensão ou sair da conta."""
    from .redes import REDES

    redes = redes_da_conta(conta)
    base = REDES["instagram" if "instagram" in redes else sorted(redes)[0]].base
    pasta = pasta_conta(conta)
    if not pasta.exists():
        aviso(f"A conta {conta!r} não existe. Para criar: python -m radar navegador-login --conta {conta}")
        return False
    if tem_sessao(pasta) is None:
        aviso(f"A conta {conta!r} está aberta em outra janela (vigilância ou busca). Pare-a antes.")
        return False
    aviso("Feche a janela do Edge quando terminar...")
    return _edge_comum(pasta, base + "/", espera_max, aviso)


def remover(conta: str) -> list[Path]:
    """Apaga o perfil do Edge da conta (o login) e a marca de desativada. O registro de leituras fica, para o teto
    continuar valendo se a mesma conta do Instagram for logada de novo com esse nome."""
    pasta = pasta_conta(conta)
    if tem_sessao(pasta) is None:
        raise RuntimeError(f"A conta {conta!r} está aberta em outra janela (vigilância ou busca). Pare-a antes.")
    apagados = []
    if pasta.exists():
        shutil.rmtree(pasta)
        apagados.append(pasta)
    if ativar(conta):
        apagados.append(arquivo_desativada(conta))
    return apagados
