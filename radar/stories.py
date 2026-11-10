"""Stories das contas monitoradas. Duram 24 horas, então o monitoramento os lê na mesma visita do dia.

O texto de um story vem de três lugares: a legenda automática de acessibilidade do Instagram (que costuma trazer o
texto escrito na imagem), os adesivos (link da matéria, menções, hashtags, local) e o OCR do Windows sobre a imagem
(nos vídeos, sobre a capa). Esse texto vira a legenda do story na tabela de posts, com tipo 'Story', e passa pelo
mesmo filtro de palavras e pela mesma IA das menções. A imagem (e o vídeo) de quem passa no filtro é salva em
data/stories/, porque o story some.
"""
import shutil
import tempfile
import uuid
from pathlib import Path

from . import config, db, mencoes, ocr

PASTA = config.DATA_DIR / "stories"
TIPO = "Story"


def texto_do_story(story: dict, texto_ocr: str = "") -> str:
    partes = [story.get("textoAlt") or "", texto_ocr or ""]
    partes += [f"Link: {link}" for link in story.get("links") or []]
    partes += [f"@{m}" for m in story.get("mencoes") or []]
    partes += [f"#{h}" for h in story.get("hashtags") or []]
    return "\n".join(p.strip() for p in partes if p and p.strip())


def ja_gravados(con, username: str) -> set[str]:
    return {l[0] for l in con.execute(
        "SELECT post_id FROM posts WHERE rede = 'instagram' AND username = ? AND tipo = ?", (username, TIPO))}


def ultimo(con, username: str):
    """Data do story mais recente já gravado da conta."""
    from . import perfis

    valor = con.execute("SELECT MAX(publicado_em) FROM posts WHERE rede = 'instagram' AND username = ? AND tipo = ?",
                        (username, TIPO)).fetchone()[0]
    return perfis.ler_data(valor)


def _gravar(con, username: str, story: dict, texto: str, arquivo: Path | None) -> None:
    con.execute(
        """INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, local, medido_em, arquivo)
           VALUES ('instagram', ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (rede, post_id) DO NOTHING""",
        (story["id"], username, story.get("url"), TIPO, story.get("timestamp"), texto, story.get("locationName"),
         db.agora(), str(arquivo) if arquivo else None))


def coletar(con, coletor, username: str, pasta: Path | None = None, ler_texto=None, triar=None) -> dict:
    """Lê os stories ativos da conta, grava os ainda não vistos e salva a mídia dos que falam de energia.

    Com `triar(textos) -> (posições marcadas, modelo)`, os stories com texto que não passaram no filtro de palavras
    vão à triagem da IA (triagem.py) antes de as imagens serem apagadas: o marcado fica com a foto, como os do filtro.
    Se a triagem falhar, eles ficam sem triagem e o `triar-stories` os pega depois."""
    from . import triagem

    pasta = pasta or PASTA
    ler_texto = ler_texto or ocr.ler_texto
    resumo = {"stories": 0, "candidatos": 0}
    vistos = ja_gravados(con, username)
    lidos = []  # (story, texto, imagem temporária): o banco só é usado no fim, sem segurar os outros trabalhadores
    for story in coletor.stories(username):
        if story["id"] in vistos:
            continue
        texto_ocr = ""
        temporaria = Path(tempfile.gettempdir()) / f"radar_story_{uuid.uuid4().hex[:8]}.jpg"
        if story.get("imagem") and coletor.baixar(story["imagem"], temporaria):
            try:
                texto_ocr = ler_texto(temporaria)
            except Exception:  # OCR indisponível ou imagem ilegível: segue com o texto que veio pronto
                texto_ocr = ""
        lidos.append((story, texto_do_story(story, texto_ocr), temporaria))

    a_triar = [i for i, (_, texto, _) in enumerate(lidos) if triagem.precisa(texto)] if triar else []
    marcados, modelo = set(), None
    if a_triar:
        try:
            posicoes, modelo = triar([lidos[i][1] for i in a_triar])
            marcados = {a_triar[p] for p in posicoes}
        except Exception:  # sem rede ou sem saldo: a coleta segue, e o triar-stories faz a triagem depois
            a_triar = []

    for i, (story, texto, temporaria) in enumerate(lidos):
        arquivo = None
        if i in marcados or mencoes.menciona_energia(texto):
            resumo["candidatos"] += 1
            destino = pasta / username
            destino.mkdir(parents=True, exist_ok=True)
            if temporaria.exists():
                arquivo = destino / f"{story['id']}.jpg"
                shutil.move(temporaria, arquivo)
            video = destino / f"{story['id']}.mp4"
            if story.get("video") and coletor.baixar(story["video"], video):
                arquivo = video
        temporaria.unlink(missing_ok=True)
        _gravar(con, username, story, texto, arquivo)
        con.commit()  # um story por vez: o download do vídeo do próximo não segura o banco dos outros trabalhadores
        resumo["stories"] += 1
    if a_triar:
        triagem.gravar(con, [lidos[i][0]["id"] for i in a_triar], {a_triar.index(i) for i in marcados}, modelo)
        resumo["triados"] = len(a_triar)
    con.commit()
    return resumo
