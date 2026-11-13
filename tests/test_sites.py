"""Sites de notícia lidos pelo feed (sites.py). A rede é falsa: cada endereço devolve um conteúdo fixo, fictício."""
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from radar import db, mencoes, sites, vigia

from conftest import analise, conta, deepseek_falso, resposta_deepseek

AGORA = datetime.now(timezone.utc)


def rss(*itens) -> bytes:
    corpo = "".join(
        f"<item><title>{t}</title><link>https://{d}/{i}</link><pubDate>{format_datetime(AGORA - timedelta(days=dias))}"
        f"</pubDate><content:encoded><![CDATA[<p>{texto}</p>]]></content:encoded></item>"
        for i, (d, t, texto, dias) in enumerate(itens))
    return (f'<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
            f"<channel>{corpo}</channel></rss>").encode()


class Rede:
    def __init__(self, paginas):
        self.paginas, self.pedidos = paginas, []

    def __call__(self, url, tempo=None):
        self.pedidos.append(url)
        if url not in self.paginas:
            raise OSError("404")
        return self.paginas[url]


def test_le_rss_atom_e_wordpress():
    [item] = sites.ler_feed(rss(("site.to", "Bairro sem energia", "A Energisa diz que &amp; religa hoje", 1)))
    assert item["titulo"] == "Bairro sem energia" and item["texto"] == "A Energisa diz que & religa hoje"
    assert item["link"] == "https://site.to/0" and item["publicado_em"].endswith("Z")
    atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Apag\xc3\xa3o</title>'
            b'<link rel="alternate" href="https://b.to/x"/><id>1</id><updated>2026-11-10T10:00:00Z</updated>'
            b"<summary>Queda de energia</summary></entry></feed>")
    assert sites.ler_feed(atom)[0]["link"] == "https://b.to/x"
    wp = json.dumps([{"link": "https://c.to/m", "date_gmt": "2026-11-10T10:00:00",
                      "title": {"rendered": "Poste caído"}, "excerpt": {"rendered": "<p>Rua sem luz</p>"}}]).encode()
    assert sites.ler_wp(wp)[0]["publicado_em"] == "2026-11-10T10:00:00Z"


def test_acha_o_feed_anunciado_ou_o_comum():
    pagina = b'<html><head><link rel="alternate" type="application/rss+xml" href="/noticias/feed/"></head></html>'
    rede = Rede({"https://a.to/": pagina, "https://a.to/noticias/feed/": rss(("a.to", "T", "x", 1)),
                 "https://b.to/": b"<html></html>", "https://b.to/rss": rss(("b.to", "T", "x", 1))})
    assert sites.achar_feed("a.to", rede) == ("rss", "https://a.to/noticias/feed/", None)
    assert sites.achar_feed("b.to", rede) == ("rss", "https://b.to/rss", None)
    assert sites.achar_feed("c.to", rede) == (None, None, "OSError")  # sem conexão: nem tenta o resto


def test_base_junta_curadoria_google_e_bio(con, municipios):
    conta(con, municipios, "portalx", [], tipo="portal_noticias", municipios=["Gurupi"])
    conta(con, municipios, "linkzeiro", [], tipo="portal_noticias", municipios=["Gurupi"])
    conta(con, municipios, "energisato", [], tipo="portal_noticias", municipios=["Gurupi"])
    con.execute("UPDATE contas SET url_externa = 'https://www.portalx.com.br/inicio' WHERE username = 'portalx'")
    con.execute("UPDATE contas SET url_externa = 'linktr.ee/linkzeiro' WHERE username = 'linkzeiro'")
    con.execute("UPDATE contas SET url_externa = 'https://energisa.com.br' WHERE username = 'energisato'")
    db.upsert_conta(con, {"username": "jornalnacional.com", "nome": "Jornal", "origem": "google_noticias"}, rede="web")
    base = sites.base_candidata(con)
    assert base["portalx.com.br"]["origem"] == "bio_instagram" and base["portalx.com.br"]["escopo"] == "to"
    assert base["jornalnacional.com"]["escopo"] == "nacional"
    assert base["conexaoto.com.br"]["origem"] == "curadoria"
    assert not {"linktr.ee", "energisa.com.br"} & set(base)  # agregador de links e a própria Energisa ficam fora


def test_rodada_grava_so_energia_do_to_analisa_e_alerta(con, municipios, monkeypatch):
    google = []
    monkeypatch.setattr(sites.noticias, "coletar", lambda con, dias=None, aviso=None: google.append(dias) or {})
    con.execute("INSERT INTO sites (dominio, nome, tipo, escopo, origem, feed, formato) VALUES "
                "('local.to', 'Portal Local', 'portal', 'to', 'bio_instagram', 'https://local.to/feed/', 'rss'), "
                "('nacional.org', 'Investigativo', 'investigativo', 'nacional', 'curadoria', 'https://nacional.org/feed/', "
                "'rss')")
    # A mesma matéria já veio pelo Google Notícias (só o título): não entra de novo.
    db.upsert_conta(con, {"username": "local.to", "nome": "Portal Local", "origem": "google_noticias"}, rede="web")
    con.execute("INSERT INTO posts (rede, post_id, username, legenda, medido_em) VALUES ('web', 'gn1', 'local.to', "
                "'Energisa anuncia obra no setor Sul', 'x')")
    rede = Rede({
        "https://local.to/feed/": rss(
            ("local.to", "Moradores ficam 12 horas sem energia", "No setor Aureny, a falta de energia...", 1),
            ("local.to", "Energisa anuncia obra no setor Sul", "obra de rede elétrica", 1),
            ("local.to", "Festa da cidade", "show no sábado", 1),
            ("local.to", "Apagão antigo", "sem energia", 40)),
        "https://nacional.org/feed/": rss(
            ("nacional.org", "Apagão no Amapá", "A falta de energia no Amapá...", 1),
            ("nacional.org", "Conta de luz no Tocantins sobe", "A tarifa de energia no Tocantins...", 1)),
    })
    pedidos = []

    def responder(params):
        pedidos.append(params["messages"][1]["content"])
        return resposta_deepseek(analise(resumo="Moradores do Aureny ficaram 12 horas sem energia."))

    cliente, _ = deepseek_falso(responder)
    resumo = sites.rodada(con, cliente, AGORA, aviso=lambda _: None, baixar_=rede)
    assert (resumo["novas"], resumo["repetidas"], resumo["sem_energia"], resumo["antigas"], resumo["fora_do_to"]) == (
        2, 1, 1, 1, 1)
    titulos = {l[0].split("\n")[0] for l in con.execute("SELECT legenda FROM posts WHERE rede = 'web'")}
    assert {"Moradores ficam 12 horas sem energia", "Conta de luz no Tocantins sobe"} <= titulos
    assert resumo["mencoes"] == 3 and len(pedidos) == 3  # as 2 novas e a do Google Notícias, ainda sem análise
    assert any("No setor Aureny" in p and mencoes.FORMATO_MATERIA in p for p in pedidos)  # o começo do texto vai junto
    assert "Aureny" in vigia.ARQUIVO_ALERTAS.read_text(encoding="utf-8")
    # A rodada é uma por hora entre os trabalhadores; o Google Notícias, a cada 6 h.
    assert sites.rodada(con, cliente, AGORA + timedelta(minutes=20), aviso=lambda _: None, baixar_=rede) == {}
    sites.rodada(con, cliente, AGORA + timedelta(hours=2), aviso=lambda _: None, baixar_=rede)
    assert google == [3]  # o Google Notícias só na primeira rodada das 6 h
    assert con.execute("SELECT lido_em IS NOT NULL, itens_ultimo FROM sites WHERE dominio = 'local.to'"
                       ).fetchone()[1] == 4


def test_sem_base_nao_ha_rodada(con):
    assert sites.rodada(con, None, AGORA, aviso=lambda _: None, baixar_=Rede({})) == {}
