import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, unquote

import pytest

from radar import capas, db, estatico, painel

from conftest import conta
from test_painel import _base, _jpeg

NODE = shutil.which("node")

# Roda o web/estatico.js no Node, com o fetch lendo os arquivos do site gravado, e devolve a resposta de cada pedido.
# "nome:<id>" devolve o nome de arquivo que o JS dá ao id; "imagem:<tipo>|<id>", o endereço da imagem.
EXECUTOR = r"""
const fs = require('fs')
const path = require('path')
const [raiz, arquivoPedidos] = process.argv.slice(2)
globalThis.window = globalThis
globalThis.fetch = async (url) => {
  const arquivo = path.join(raiz, url)
  if (!fs.existsSync(arquivo)) return { ok: false, status: 404 }
  return { ok: true, status: 200, json: async () => JSON.parse(fs.readFileSync(arquivo, 'utf8')) }
}
require(path.join(raiz, 'estatico.js'))
;(async () => {
  const saida = []
  for (const pedido of JSON.parse(fs.readFileSync(arquivoPedidos, 'utf8'))) {
    if (pedido.startsWith('nome:')) { saida.push(window.radarEstatico.nomeArquivo(pedido.slice(5))); continue }
    if (pedido.startsWith('imagem:')) { saida.push(window.radarEstatico.imagem(...pedido.slice(7).split('|'))); continue }
    try { saida.push(await window.radarEstatico.api(pedido)) } catch (erro) { saida.push({ erro: erro.status ?? String(erro) }) }
  }
  process.stdout.write(JSON.stringify(saida))
})()
"""


@pytest.fixture(autouse=True)
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(capas, "PASTA", tmp_path / "capas")
    monkeypatch.setattr(capas, "STORIES", tmp_path / "stories")


def _servidor(con, pedido: str, agora, feito: dict) -> dict:
    """O que o servidor responderia ao pedido (como o do-GET o lê), com os arquivos que o site `feito` tem."""
    rota, _, consulta = pedido.partition("?")
    q = {k: v[-1] for k, v in parse_qs(consulta).items()}
    dados = painel.responder(con, unquote(rota), q, agora)
    if dados is None:
        return {"erro": 404}
    return json.loads(json.dumps(estatico.para_o_site(dados, feito["com_capa"], feito["com_foto"], feito["midias"])))


def _baixadas(*, capas_de=(), fotos_de=()) -> None:
    """Capas e fotos como se o painel já as tivesse baixado para data/capas/."""
    for post_id in capas_de:
        destino = capas.arquivo_post(post_id)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(_jpeg())
    for username in fotos_de:
        destino = capas.arquivo_foto(username)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(_jpeg())


def _site(tmp_path, pedidos: list[str]) -> list:
    executor = tmp_path / "executor.js"
    executor.write_text(EXECUTOR, encoding="utf-8")
    lista = tmp_path / "pedidos.json"
    lista.write_text(json.dumps(pedidos), encoding="utf-8")
    saida = subprocess.run([NODE, str(executor), str(tmp_path / "site"), str(lista)], capture_output=True,
                           encoding="utf-8", check=True, timeout=120)
    return json.loads(saida.stdout)


def _conferir(con, tmp_path, pedidos: list[str], agora, feito: dict) -> None:
    for pedido, resposta in zip(pedidos, _site(tmp_path, pedidos), strict=True):
        assert resposta == _servidor(con, pedido, agora, feito), pedido


def _muitas(con, municipios, n: int = 50) -> str:
    """Um portal com `n` menções, uma a cada 5 horas, e uma matéria de site com o id comprido do Google Notícias."""
    conta(con, municipios, "portalto", [], seguidores=5_000)
    agora = datetime.now(timezone.utc)
    for i in range(n):
        data = (agora - timedelta(hours=5 * i + 1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        con.execute("INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, curtidas, "
                    "comentarios, medido_em) VALUES ('instagram', ?, 'portalto', ?, 'Image', ?, ?, ?, 0, 'x')",
                    (f"p{i}", f"https://www.instagram.com/p/p{i}/", data, f"Falta de energia no bairro {i}", 7 * i % 23))
        con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, tema, sentimento, alcance, "
                    "resumo, setor, analisada_em) VALUES ('instagram', ?, 1, ?, 'falta_energia', ?, 'local', ?, "
                    "'operacao', 'x')", (f"p{i}", i % 2, ("negativo", "neutro", "positivo")[i % 3], f"Resumo {i}"))
    materia = "CBMi" + "QUFV" * 70 + "Xy"
    con.execute("INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, medido_em) VALUES "
                "('web', ?, 'jornal.exemplo.com.br', 'https://jornal.exemplo.com.br/x', 'Materia', ?, "
                "'Energisa anuncia obra em Palmas', 'x')", (materia, agora.strftime("%Y-%m-%dT%H:%M:%S.000Z")))
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, tema, sentimento, alcance, resumo, "
                "setor, analisada_em) VALUES ('web', ?, 1, 1, 'obras_investimento', 'positivo', 'estadual', "
                "'Obra nova em Palmas.', 'obras_expansao', 'x')", (materia,))
    con.commit()
    return materia


def test_nome_de_arquivo_curto_fica_e_o_resto_vira_hash():
    assert estatico.fnv1a("") == 0x811C9DC5 and estatico.fnv1a("a") == 0xE40C292C
    assert estatico.fnv1a("foobar") == 0xBF9CF968
    assert estatico.nome_arquivo("4019642821613871770") == "4019642821613871770"
    assert estatico.nome_arquivo("conta-jornal.do_to") == "conta-jornal.do_to"
    assert estatico.nome_arquivo("A") == "~c40bf6cc"  # maiúscula: no Windows, "A" e "a" seriam o mesmo arquivo
    assert estatico.nome_arquivo("x" * 65).startswith("~") and estatico.nome_arquivo(".oculto").startswith("~")


def test_site_tem_a_pagina_os_dados_e_as_imagens_baixadas(con, municipios, tmp_path):
    from PIL import Image

    _base(con, municipios)
    _baixadas(capas_de=["id-G1"], fotos_de=["gurupinoticias", "naomonitorada", "dabase", "descartada"])
    video = capas.STORIES / "gurupinoticias" / "s2.mp4"  # o vídeo do story que o coletor salvou
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    # Conta no banco que o painel não lista (nem monitorada nem autora de menção) e sem cidade: fica fora do site.
    db.upsert_conta(con, {"username": "naomonitorada", "nome": "Fora", "origem": "busca"})
    # Ligada pela IA a Gurupi, fora do monitoramento: é um ponto do mapa da base e ganha página; a descartada, não.
    for username in ("dabase", "descartada"):
        conta(con, municipios, username, [], prioridade="C")
    con.execute("UPDATE contas SET descartada = 'comercial' WHERE username = 'descartada'")
    con.commit()
    resumo = estatico.exportar(con, tmp_path / "site")
    site = tmp_path / "site"
    assert resumo["mencoes"] == 2 and resumo["contas"] == 1 and resumo["arquivos"] > 100
    assert painel.conta(con, "naomonitorada") is not None and not (site / "api" / "conta" / "naomonitorada.json").exists()
    assert not (site / "foto" / "naomonitorada.jpg").exists()
    mapa_da_base = json.loads((site / "api" / "mapa" / "base.json").read_text(encoding="utf-8"))
    assert {p["username"] for p in mapa_da_base["pontos"]} == {"gurupinoticias", "dabase"}
    assert (site / "api" / "conta" / "dabase.json").exists() and (site / "foto" / "dabase.jpg").exists()
    assert not (site / "api" / "conta" / "descartada.json").exists() and not (site / "foto" / "descartada.jpg").exists()
    # O vídeo do story vai para midia/, e a ficha aponta para ele.
    assert resumo["midias"] == {"s2": {"tipo": "video", "url": "midia/s2.mp4"}}
    assert (site / "midia" / "s2.mp4").read_bytes() == video.read_bytes()
    story = json.loads((site / "api" / "post" / "s2.json").read_text(encoding="utf-8"))
    assert story["midia"] == {"tipo": "video", "url": "midia/s2.mp4"} and painel.post(con, "s2")["midia"]["url"] == "/midia/s2"
    html = (site / "index.html").read_text(encoding="utf-8")
    assert html.index('src="estatico.js"') < html.index('src="app.js"') and (site / ".nojekyll").exists()
    assert (site / "fontes" / "OFL.txt").exists() and (site / "marca" / "energisa.svg").exists()
    # A capa baixada vai como está; a do story sai da imagem do OCR, reduzida, direto no site (data/capas fica igual).
    assert resumo["com_capa"] == {"id-G1", "s2"} and resumo["com_foto"] == {"gurupinoticias", "dabase"}
    capa_g1 = site / "capa" / f"{estatico.nome_arquivo('id-G1')}.jpg"  # tem maiúscula: o nome é o hash
    assert capa_g1.name.startswith("~") and capa_g1.read_bytes() == capas.arquivo_post("id-G1").read_bytes()
    with Image.open(site / "capa" / "s2.jpg") as capa_story:
        assert capa_story.width == capas.LARGURA_POST
    assert not capas.arquivo_post("s2").exists() and (site / "foto" / "gurupinoticias.jpg").exists()
    itens = json.loads((site / "api" / "itens.json").read_text(encoding="utf-8"))
    assert set(itens) == {"id-G1", "s2"} and all(p["capa"] for p in itens.values())  # só menções
    recentes = json.loads((site / "api" / "linha" / "recentes.json").read_text(encoding="utf-8"))
    assert recentes["periodos"] == [1, 3, 7]
    assert set(recentes["combos"]) == {f"{d}||{o}" for d in (1, 3, 7) for o in ("recentes", "interacoes", "views")}
    conta_site = json.loads((site / "api" / "conta" / "gurupinoticias.json").read_text(encoding="utf-8"))
    assert conta_site["foto"] is True
    assert json.loads((site / "api" / "status.json").read_text(encoding="utf-8"))["estatico"] is True
    vazia = json.loads((site / "api" / "linha" / "setor-rh.json").read_text(encoding="utf-8"))
    assert vazia["vazia"] is True and vazia["combos"] == {}
    # De novo na mesma pasta: os dados e as imagens antigos saem. Pasta com outros arquivos não é tocada.
    (site / "api" / "post" / "velho.json").write_text("{}", encoding="utf-8")
    (site / "capa" / "velha.jpg").write_bytes(_jpeg())
    capas.arquivo_foto("gurupinoticias").unlink()
    capas.arquivo_foto("dabase").unlink()
    resumo = estatico.exportar(con, site)
    assert not (site / "api" / "post" / "velho.json").exists() and not (site / "capa" / "velha.jpg").exists()
    assert resumo["com_foto"] == set() and not (site / "foto").exists()
    conta_site = json.loads((site / "api" / "conta" / "gurupinoticias.json").read_text(encoding="utf-8"))
    assert conta_site["foto"] is False  # sem a foto no site, o perfil mostra as iniciais
    outra = tmp_path / "outra"
    outra.mkdir()
    (outra / "nota.txt").write_text("minha", encoding="utf-8")
    with pytest.raises(FileExistsError):
        estatico.exportar(con, outra)
    assert (outra / "nota.txt").read_text(encoding="utf-8") == "minha"


@pytest.mark.skipif(not NODE, reason="precisa do Node para rodar o estatico.js")
def test_estatico_js_responde_como_o_servidor(con, municipios, tmp_path):
    _base(con, municipios)
    _baixadas(capas_de=["id-G1"], fotos_de=["gurupinoticias", "dabase"])
    (capas.STORIES / "gurupinoticias" / "s2.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    conta(con, municipios, "dabase", [], prioridade="C", seguidores=900, municipios=["Palmas"])
    # O apagão e o story são a mesma notícia: as fileiras mostram um cartão só, com a soma, e o site também.
    fato_id = con.execute("INSERT INTO fatos (titulo, primeira_em, ultima_em, criado_em, atualizado_em) "
                          "VALUES ('x', 'x', 'x', 'x', 'x')").lastrowid
    for post_id in ("id-G1", "s2"):
        con.execute("INSERT INTO fato_mencao (rede, post_id, fato_id, como, ligado_em) "
                    "VALUES ('instagram', ?, ?, 'manual', 'x')", (post_id, fato_id))
    con.commit()
    agora = painel._agora()
    feito = estatico.exportar(con, tmp_path / "site", agora)
    assert [p["id"] for p in painel.linha(con, "mencoes", agora=agora)["itens"]] == ["s2"]
    assert feito["midias"] == {"s2": {"tipo": "video", "url": "midia/s2.mp4"}}
    gurupi = next(i for i, nome in painel._cidades(con) if nome == "Gurupi")
    pedidos = [
        "status", "inicio", "mapa-base",
        "linha/mencoes", "linha/mencoes?sentimento=negativo", "linha/mencoes?sentimento=&dias=7",
        "linha/mencoes?dias=todos&sentimento=positivo", "linha/mencoes?dias=abc&ordem=nenhuma&pagina=x",
        "linha/negativas?sentimento=", "linha/negativas?sentimento=neutro&dias=30", "linha/mencoes?sentimento=xyz",
        "linha/top10", "linha/top10?dias=todos&ordem=recentes", "linha/reels?ordem=views&dias=30",
        "linha/recentes", "linha/recentes?dias=3", "linha/recentes?dias=todos&sentimento=negativo",
        "linha/recentes?dias=7&ordem=interacoes&pagina=0", "linha/recentes?dias=30&ordem=x", "linha/recentes?dias=3&dias=",
        "linha/recentes?pagina=1", "post/id-G3",
        "linha/stories?pagina=1", "linha/diretas?ordem=interacoes&dias=90", "linha/indiretas?dias=7&dias=todos",
        "linha/setor-operacao", "linha/setor-rh?dias=7", f"linha/municipio-{gurupi}", f"linha/cidade-{gurupi}?dias=7",
        "linha/conta-gurupinoticias?pagina=0", "linha/nao-existe", "linha/conta-..%2Fx",
        "post/id-G1", "post/s2", "post/id-G2", "post/nao-existe",
        "conta/gurupinoticias", "conta/GurupiNoticias", "conta/ninguem",
        f"municipio/{gurupi}", "municipio/1", "municipio/abc",
        "contas", "contas?tipo=&ordem=mencoes&pagina=0", "contas?tipo=portal_noticias&ordem=seguidores",
        "contas?tipo=influenciador", "contas?tipo=xyz&ordem=qualquer",
        "setores", "setores?dias=30&sentimento=negativo", "setores?dias=todos&sentimento=x",
        "mapa", "mapa?camada=mencoes&dias=7&sentimento=negativo", "mapa?camada=perfis",
        "mapa?camada=mencoes&sentimento=xyz", "mapa?camada=outra&dias=90", "mapa?camada=base",
        "mapa?camada=base&dias=7&sentimento=negativo", "conta/dabase",
        "busca?q=apagao", "busca?q=" + quote("cadê a ENERGISA"), "busca?q=gurupinot", "busca?q=a", "busca?q=100%25",
        "busca?q=", "nada",
    ]
    respostas = _site(tmp_path, pedidos)
    assert respostas[0] == _servidor(con, "status", agora, feito) | {"estatico": True}
    for pedido, resposta in zip(pedidos[1:], respostas[1:], strict=True):
        assert resposta == _servidor(con, pedido, agora, feito), pedido
    # O endereço de cada imagem que o JS monta é um arquivo do site.
    imagens = [("capa", "id-G1"), ("capa", "s2"), ("foto", "gurupinoticias"), ("foto", "dabase")]
    for caminho in _site(tmp_path, [f"imagem:{tipo}|{chave}" for tipo, chave in imagens]):
        assert (tmp_path / "site" / caminho).is_file(), caminho


@pytest.mark.skipif(not NODE, reason="precisa do Node para rodar o estatico.js")
def test_estatico_js_pagina_e_acha_materia_de_id_comprido(con, municipios, tmp_path):
    materia = _muitas(con, municipios)
    _baixadas(capas_de=["p0", "p7", "p30"], fotos_de=["portalto"])
    agora = painel._agora()
    feito = estatico.exportar(con, tmp_path / "site", agora)
    assert feito["com_capa"] == {"p0", "p7", "p30"} and feito["com_foto"] == {"portalto"}
    assert (tmp_path / "site" / "api" / "post" / f"{estatico.nome_arquivo(materia)}.json").exists()
    pedidos = [
        "linha/mencoes", "linha/mencoes?pagina=1", "linha/mencoes?pagina=2", "linha/mencoes?dias=7&pagina=0",
        "linha/mencoes?sentimento=neutro&dias=30", "linha/top10?dias=todos&pagina=1", "linha/conta-portalto?pagina=1",
        "linha/setor-operacao?sentimento=positivo", "linha/noticias", "linha/setor-obras_expansao",
        "contas?ordem=mencoes", "setores?dias=7", "mapa?dias=7&sentimento=neutro", "conta/portalto",
        "linha/recentes?dias=7", "linha/recentes?dias=3&ordem=interacoes",
        "busca?q=bairro", "busca?q=palmas", "post/" + quote(materia), "post/p0",
    ]
    _conferir(con, tmp_path, pedidos, agora, feito)
    nomes =["abc", "Abc", "çã", "x" * 64, "x" * 65, ".x", "conta-a.b", "", materia]
    assert _site(tmp_path, [f"nome:{n}" for n in nomes]) == [estatico.nome_arquivo(n) for n in nomes]
