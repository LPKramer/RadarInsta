"""Threads e X pelo navegador (Fase 3). Cada rede tem a sua conta, logada no Edge como as do Instagram: a `conta7` só
no Threads e a `x1` só no X (o arquivo data/navegador/redes-<conta>.txt diz as redes de cada conta; sem ele, a conta é
só do Instagram). O ponto de partida é a menção: a busca por palavra-chave (busca_posts.py) acha os posts e a
investigação (investigacao.py) situa quem postou.

Como no Instagram, o radar não chama endereços por conta própria: abre as páginas e lê as respostas que o próprio
site recebe. O formato delas é interno e muda; o piloto (`python -m radar piloto-rede`) guarda tudo o que veio numa
busca e num perfil, para os leitores serem escritos sobre amostras reais.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from . import config


class BloqueioRede(Exception):
    """A rede barrou a conta (login pedido, verificação, limite). Não é a ColetaInterrompida do Instagram: pausa só
    aquela rede, sem parar a vigilância nem desativar a conta."""


@dataclass(frozen=True)
class Rede:
    nome: str
    titulo: str
    base: str
    login: str
    dominios: tuple[str, ...]  # onde fica o cookie da sessão
    cookie: str  # o cookie que prova o login
    prefixo: str  # do post_id no banco (o Instagram fica sem)
    urls_bloqueio: tuple[str, ...]

    def url_busca(self, termo: str) -> str:
        if self.nome == "x":  # a aba "Mais recentes"
            return f"{self.base}/search?q={quote(termo)}&src=typed_query&f=live"
        if self.nome == "threads":
            return f"{self.base}/search?q={quote(termo)}&serp_type=default&filter=recent"
        return f"{self.base}/explore/search/keyword/?q={quote(termo)}"

    def url_perfil(self, username: str) -> str:
        return f"{self.base}/@{username}" if self.nome == "threads" else f"{self.base}/{username}/"


REDES = {
    "instagram": Rede("instagram", "Instagram", "https://www.instagram.com", "https://www.instagram.com/accounts/login/",
                      ("instagram.com",), "sessionid", "",
                      ("/accounts/login", "/challenge", "/accounts/suspended", "/checkpoint")),
    "threads": Rede("threads", "Threads", "https://www.threads.com", "https://www.threads.com/login",
                    ("threads.com", "threads.net"), "sessionid", "th_",
                    ("/login", "/challenge", "/accounts/suspended", "/checkpoint")),
    "x": Rede("x", "X", "https://x.com", "https://x.com/i/flow/login", ("x.com", "twitter.com"), "auth_token", "x_",
              ("/i/flow/login", "/account/access", "/account/suspended")),
}


# --- Página no Edge (piloto e, depois, os leitores) ------------------------------------------

class PaginaRede:
    """O Edge da conta, controlado pelo Playwright, numa rede. Guarda as respostas JSON que o site recebe (graphql e
    api) e, a pedido, o JSON embutido no HTML (o Threads manda parte dos dados assim)."""

    def __init__(self, rede: Rede, pasta: Path, visivel: bool = True):
        self.rede, self.pasta, self.visivel = rede, pasta, visivel
        self.capturas: list[dict] = []

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self.contexto = self._playwright.chromium.launch_persistent_context(
            str(self.pasta), channel="msedge", headless=not self.visivel, locale="pt-BR",
            viewport={"width": 1280, "height": 900})
        self.pagina = self.contexto.pages[0] if self.contexto.pages else self.contexto.new_page()
        self.pagina.on("response", self._registrar)
        self.pagina.goto(self.rede.base + "/", wait_until="domcontentloaded")
        return self

    def __exit__(self, *_):
        try:
            self.contexto.close()
        except Exception:  # a janela já foi fechada
            pass
        finally:
            self._playwright.stop()

    def logado(self) -> bool:
        return any(c["name"] == self.rede.cookie and c.get("value") for c in self.contexto.cookies(self.rede.base))

    def _registrar(self, resposta) -> None:
        url = resposta.url
        if not any(t in url for t in ("graphql", "/api/")):
            return
        try:
            corpo = resposta.json()
        except Exception:
            return
        self.capturas.append({"url": url.split("?")[0][:200], "status": resposta.status, "corpo": corpo})

    def _conferir(self) -> None:
        url = self.pagina.url
        if any(t in url for t in self.rede.urls_bloqueio):
            raise BloqueioRede(f"O {self.rede.titulo} levou a conta para {url}. Resolva na janela do Edge "
                               f"(python -m radar navegador-abrir) e tente de novo mais tarde.")

    def embutidos(self) -> list:
        """Os blocos <script type="application/json"> do HTML, já lidos."""
        textos = self.pagina.evaluate(
            "() => [...document.querySelectorAll('script[type=\"application/json\"]')].map(s => s.textContent)")
        blocos = []
        for texto in textos:
            try:
                blocos.append(json.loads(texto))
            except ValueError:
                continue
        return blocos

    def visitar(self, url: str, segundos: float = 8, rolagens: int = 1) -> dict:
        """Abre a página, espera os dados chegarem (rolando um pouco) e devolve o que veio."""
        self.capturas = []
        self.pagina.goto(url, wait_until="domcontentloaded")
        self.pagina.wait_for_timeout(segundos * 1000)
        self._conferir()
        for _ in range(rolagens):
            self.pagina.mouse.wheel(0, 5000)
            self.pagina.wait_for_timeout(3000)
        return {"url": url, "final": self.pagina.url, "capturas": self.capturas, "embutidos": self.embutidos()}


def piloto(rede: str, pasta: Path, termo: str, perfil: str | None = None, visivel: bool = True) -> Path:
    """Uma busca e, se pedido, um perfil, com tudo o que o site mandou guardado em data/raw/ (fora do git). Duas
    leituras no máximo."""
    r = REDES[rede]
    with PaginaRede(r, pasta, visivel) as pagina:
        if not pagina.logado():
            raise BloqueioRede(f"A conta não está logada no {r.titulo}. Rode: python -m radar navegador-login "
                               f"--conta <conta> --rede {rede}")
        resultado = {"rede": rede, "termo": termo, "busca": pagina.visitar(r.url_busca(termo))}
        if perfil:
            time.sleep(5)
            resultado["perfil"] = pagina.visitar(r.url_perfil(perfil.lstrip("@")), rolagens=0)
    caminho = config.RAW_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_piloto_{rede}.json"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    return caminho


def resumo_piloto(caminho: Path) -> list[str]:
    """O que veio no piloto: cada resposta com o endereço e as primeiras chaves de `data`."""
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    linhas = []
    for parte in ("busca", "perfil"):
        visita = dados.get(parte)
        if not visita:
            continue
        linhas.append(f"{parte}: {visita['final']} ({len(visita['capturas'])} respostas, "
                      f"{len(visita['embutidos'])} blocos no HTML)")
        for c in visita["capturas"]:
            corpo = c["corpo"] if isinstance(c["corpo"], dict) else {}
            chaves = list((corpo.get("data") or corpo or {}).keys())[:4] if isinstance(corpo.get("data") or corpo,
                                                                                      dict) else []
            linhas.append(f"  {c['status']} {c['url']}  {chaves}")
    return linhas
