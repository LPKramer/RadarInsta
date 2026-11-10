import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar import config, mencoes as mc, monitoramento as mon, navegador as nav, stories

from conftest import conta, post


def item_story(pk: int, horas_atras: float = 2, video: bool = False, **extra) -> dict:
    """Story como vem em `reels_media[].items[]` (formato visto no site em 4/nov/2026)."""
    quando = datetime.now(timezone.utc) - timedelta(hours=horas_atras)
    item = {
        "pk": str(pk), "taken_at": int(quando.timestamp()), "media_type": 2 if video else 1,
        "image_versions2": {"candidates": [{"width": 640, "url": f"https://cdn/{pk}_p.jpg"},
                                           {"width": 1206, "url": f"https://cdn/{pk}_g.jpg"}]},
        "video_versions": [{"url": f"https://cdn/{pk}.mp4"}] if video else [],
        "accessibility_caption": "", "story_link_stickers": None, "story_bloks_stickers": None,
        "story_hashtags": None, "story_locations": None, "reel_mentions": None,
    }
    return item | extra


def test_story_do_site_junta_midia_e_adesivos():
    item = item_story(1, video=True,
                      story_bloks_stickers=[{"bloks_sticker": {"sticker_data": {"ig_mention": {
                          "username": "energisatocantins", "full_name": "Energisa Tocantins"}}}}],
                      story_link_stickers=[{"story_link": {"url": "https://portal.com/falta-de-energia-no-centro"}}],
                      story_locations=[{"location": {"name": "Palmas, Tocantins"}}])
    story = nav.story_do_site(item, "portal")
    assert story["imagem"] == "https://cdn/1_g.jpg" and story["video"] == "https://cdn/1.mp4"
    assert story["mencoes"] == ["energisatocantins (Energisa Tocantins)"]
    assert story["locationName"] == "Palmas, Tocantins"
    assert story["url"] == "https://www.instagram.com/stories/portal/1/"
    texto = stories.texto_do_story(story, "Queda de luz no Plano Diretor")
    assert "Queda de luz" in texto and "@energisatocantins" in texto and "falta-de-energia" in texto


def test_stories_vem_do_html_da_pagina():
    dados = {"require": [["x", "y", None, [{"__bbox": {"result": {"data": {"xdt_api__v1__feed__reels_media": {
        "reels_media": [{"user": {"username": "outra"}, "items": [item_story(9)]},
                        {"user": {"username": "Portal"}, "items": [item_story(1), item_story(2)]}]}}}}}]]]}
    html = f'<html><script type="application/json" data-sjs>{json.dumps(dados)}</script></html>'
    assert [i["pk"] for i in nav.stories_do_html(html, "portal")] == ["1", "2"]
    assert nav.stories_do_html("<html></html>", "portal") == []


class ColetorFalso:
    def __init__(self, itens, novo=True):
        self.itens = itens
        self.novo = novo
        self.baixados = []

    def stories(self, username):
        return [nav.story_do_site(i, username) for i in self.itens]

    def baixar(self, url, destino):
        Path(destino).write_bytes(b"midia")
        self.baixados.append(url)
        return True

    def story_novo(self, username, ultimo):
        return self.novo

    def rodar(self, ator, entrada, rotulo, max_usd=None):
        return "falso", []


def test_coletar_le_o_texto_e_salva_so_os_de_energia(con, municipios, tmp_path):
    conta(con, municipios, "portal", [])
    textos = {"https://cdn/1_g.jpg": "URGENTE Falta de energia atinge bairros de Araguaína",
              "https://cdn/2_g.jpg": "Promoção de sapatos só hoje"}
    coletor = ColetorFalso([item_story(1), item_story(2, video=True)])
    ler_texto = lambda caminho: textos[coletor.baixados[-1]]  # noqa: E731
    resumo = stories.coletar(con, coletor, "portal", pasta=tmp_path, ler_texto=ler_texto)
    assert resumo == {"stories": 2, "candidatos": 1}

    linhas = {l["post_id"]: dict(l) for l in con.execute("SELECT * FROM posts WHERE tipo = 'Story'")}
    assert "Falta de energia" in linhas["1"]["legenda"] and linhas["1"]["arquivo"] == str(tmp_path / "portal" / "1.jpg")
    assert (tmp_path / "portal" / "1.jpg").exists()
    assert linhas["2"]["arquivo"] is None and not (tmp_path / "portal" / "2.mp4").exists()  # sem energia: não salva
    assert stories.coletar(con, coletor, "portal", pasta=tmp_path, ler_texto=ler_texto)["stories"] == 0  # já vistos

    (candidato,) = mc.candidatos(con)  # o story entra na análise de menções como um post
    assert candidato["post_id"] == "1" and "Formato: story" in mc.descrever(candidato)


def test_monitoramento_le_os_stories_na_mesma_visita(con, municipios, tmp_path, monkeypatch):
    monkeypatch.setattr(stories, "PASTA", tmp_path)
    conta(con, municipios, "portal", [post(1, codigo="P1")])
    adesivo = [{"bloks_sticker": {"sticker_data": {"ig_mention": {"username": "energisatocantins"}}}}]
    coletor = ColetorFalso([item_story(5, story_bloks_stickers=adesivo)])
    resumo = mon.coletar_novos(con, coletor, mon.monitoradas(con), aviso=lambda _: None)
    assert resumo["stories"] == 1 and resumo["stories_sobre_energia"] == 1
    assert (tmp_path / "portal" / "5.jpg").exists()

    coletor.novo = False  # sem story novo, a página de stories nem é aberta
    assert mon.coletar_novos(con, coletor, mon.monitoradas(con), aviso=lambda _: None)["stories"] == 0
    assert mon.coletar_novos(con, ColetorFalso([item_story(6)]), mon.monitoradas(con), aviso=lambda _: None,
                             com_stories=False)["stories"] == 0


def test_story_novo_usa_a_ultima_visita_ao_perfil(tmp_path):
    agora = int(datetime.now(timezone.utc).timestamp())
    navegador = nav.Navegador(None, nav.Ritmo(dormir=lambda _: None), raw_dir=tmp_path)
    navegador._ultimo_story = {"recente": agora - 3600, "velho": agora - 90000}
    assert navegador.story_novo("recente", None)
    assert not navegador.story_novo("recente", datetime.now(timezone.utc))  # já gravado
    assert not navegador.story_novo("velho", None) and not navegador.story_novo("sem_visita", None)
    assert config.ATOR_POSTS  # o módulo de config segue importável junto


def test_link_do_story_sai_sem_o_embrulho_do_instagram():
    embrulhado = ("https://l.instagram.com/?u=https%3A%2F%2Fafnoticias.com.br%2Festado%2Ffalta-de-energia-no-centro"
                  "%3Ffbclid%3DPAZXh0bg%26id%3D7&e=AT0")
    assert nav.link_real(embrulhado) == "https://afnoticias.com.br/estado/falta-de-energia-no-centro?id=7"
    assert nav.link_real("https://portal.com/materia") == "https://portal.com/materia"


def test_ocr_funciona_dentro_de_um_laco_de_eventos(tmp_path):
    import asyncio

    from radar import ocr

    if not ocr.disponivel():
        return  # fora do Windows (ou sem o idioma pt-BR) não há o que testar
    from PIL import Image, ImageDraw, ImageFont

    imagem = tmp_path / "story.jpg"
    tela = Image.new("RGB", (1080, 600), (20, 30, 45))
    ImageDraw.Draw(tela).text((60, 250), "Falta de energia no centro", font=ImageFont.truetype("arialbd.ttf", 64),
                              fill="white")
    tela.save(imagem)

    async def dentro_do_laco():  # como na coleta, que roda dentro do laço do Playwright
        return ocr.ler_texto(imagem)

    assert "energia" in asyncio.run(dentro_do_laco()).lower()

