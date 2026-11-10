"""O gabarito: a prova do radar com casos reais, como pede o enova IA.

O autor marca a resposta certa de cada caso na planilha (DemoDay/gabarito/Gabarito_Radar_v0.xlsx, aba Casos) ANTES
de ver o que a IA respondeu. `rodar` passa cada caso pela mesma análise das menções (`mencoes.analisar_um`), num
pedido isolado, e guarda as respostas num arquivo à parte, para o autor não ver enquanto marca. `conferir` compara as
duas e escreve a planilha do resultado, caso a caso, com o acerto por dificuldade.

Critério de acerto (escrito na planilha antes do teste): o caso está certo quando a IA concorda com o autor em "sobre
energia" e, se for sobre energia, também no sentimento, que juntos decidem o alerta. "Não dá para saber" está certo
quando a IA não gera alerta. Tema e município contam à parte.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from . import classificacao_deepseek as ds, config, mencoes, municipios as mun

PASTA = config.DATA_DIR.parent / "DemoDay" / "gabarito"
PLANILHA = PASTA / "Gabarito_Radar_v0.xlsx"
RESPOSTAS = PASTA / "Respostas_da_IA_NAO_ABRA_antes_de_marcar.xlsx"
RESULTADO = PASTA / "Gabarito_Radar_resultado.xlsx"
TEMAS = {"falta de energia": "falta_energia", "desligamento programado": "desligamento_programado",
         "conta e tarifa": "conta_tarifa", "atendimento": "atendimento", "obras e investimento": "obras_investimento",
         "acidente e segurança": "acidente_seguranca", "iluminação pública": "iluminacao_publica",
         "institucional": "institucional", "outro": "outro"}
NOME_TEMA = {v: k for k, v in TEMAS.items()}
DIFICULDADES = ["fácil", "típico", "difícil", "sem resposta possível"]
SQL_POST = """
SELECT p.*, k.tipo AS tipo_conta,
       (SELECT GROUP_CONCAT(m.nome, ', ') FROM conta_municipio cm JOIN municipios m ON m.ibge_id = cm.ibge_id
         WHERE cm.rede = p.rede AND cm.username = p.username AND cm.fonte = 'ia') AS municipios_conta
FROM posts p LEFT JOIN classificacoes k ON k.rede = p.rede AND k.username = p.username
WHERE p.rede = ? AND p.post_id = ?"""


def _texto(valor) -> str:
    return " ".join(str(valor or "").split()).strip().lower()


def ler_casos(caminho: Path = PLANILHA) -> list[dict]:
    """Os casos da aba Casos, com a resposta que o autor marcou (vazia se ainda não marcou)."""
    from openpyxl import load_workbook

    aba = load_workbook(caminho, read_only=True)["Casos"]
    casos = []
    for linha in aba.iter_rows(min_row=2, values_only=True):
        if not linha or linha[0] is None:
            continue
        rede, _, post_id = str(linha[14]).partition(":")
        casos.append({"caso": int(linha[0]), "dificuldade": _texto(linha[1]), "rede": rede, "post_id": post_id,
                      "esperado": {"sobre": _texto(linha[9]), "sentimento": _texto(linha[10]),
                                   "tema": TEMAS.get(_texto(linha[11])), "municipio": _texto(linha[12])}})
    return casos


def versao(municipios) -> str:
    """A versão das instruções da IA: muda sempre que o prompt das menções muda."""
    return "regras " + hashlib.sha256(mencoes.sistema(municipios).encode("utf-8")).hexdigest()[:8]


def rodar(con, cliente, casos: list[dict], modelo: str = config.MODELO, aviso=print) -> list[dict]:
    """Cada caso num pedido isolado à IA, com as mesmas instruções da vigilância. Nada é gravado no banco."""
    municipios = mun.carregar(con)
    texto_sistema = mencoes.sistema(municipios)
    tarefas = {}
    for caso in casos:
        post = con.execute(SQL_POST, (caso["rede"], caso["post_id"])).fetchone()
        if post is None:
            aviso(f"caso {caso['caso']}: post {caso['post_id']} não está no banco")
            continue
        tarefas[caso["caso"]] = partial(mencoes.analisar_um, cliente, dict(post), texto_sistema, modelo)
    respostas = {}
    ds.em_paralelo(tarefas, lambda caso, resultado: respostas.__setitem__(caso, resultado), aviso=aviso,
                   nome="casos respondidos")
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rotulo = versao(municipios)
    return [{"caso": c, **dados, "modelo": modelo_resposta, "versao": rotulo, "respondido_em": agora}
            for c, (dados, modelo_resposta) in sorted(respostas.items())]


def acertou(esperado: dict, ia: dict) -> bool | None:
    """O critério principal. None quando o autor ainda não marcou o caso."""
    alerta = ia["sobre_energia"] and ia["sentimento"] == "negativo"
    if esperado["sobre"] == "não dá para saber":
        return not alerta
    if esperado["sobre"] == "não":
        return not ia["sobre_energia"]
    if esperado["sobre"] == "sim":
        return ia["sobre_energia"] and ia["sentimento"] == esperado["sentimento"]
    return None


def conferir(casos: list[dict], respostas: list[dict]) -> dict:
    """O acerto caso a caso e por dificuldade, com o tema e o município como acerto secundário."""
    por_caso = {r["caso"]: r for r in respostas}
    linhas, total = [], {d: [0, 0] for d in DIFICULDADES}
    secundario = {"tema": [0, 0], "municipio": [0, 0]}
    for caso in casos:
        ia = por_caso.get(caso["caso"])
        certo = acertou(caso["esperado"], ia) if ia else None
        if certo is not None:
            conta = total.setdefault(caso["dificuldade"], [0, 0])
            conta[0] += int(certo)
            conta[1] += 1
        if ia and caso["esperado"]["sobre"] == "sim" and ia["sobre_energia"]:
            if caso["esperado"]["tema"]:
                secundario["tema"][0] += int(ia["tema"] == caso["esperado"]["tema"])
                secundario["tema"][1] += 1
            if caso["esperado"]["municipio"]:
                citados = {mun.normalizar(m) for m in ia["municipios"]}
                secundario["municipio"][0] += int(mun.normalizar(caso["esperado"]["municipio"]) in citados)
                secundario["municipio"][1] += 1
        linhas.append({**caso, "ia": ia, "certo": certo})
    certos = sum(c for c, _ in total.values())
    marcados = sum(n for _, n in total.values())
    return {"linhas": linhas, "por_dificuldade": total, "certos": certos, "marcados": marcados,
            "secundario": secundario}


COLUNAS_RESPOSTA = ["caso", "sobre_energia", "sentimento", "tema", "setor", "alcance", "municipios", "resumo",
                    "confianca", "modelo", "versao", "respondido_em"]


def salvar_respostas(respostas: list[dict], caminho: Path = RESPOSTAS) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    aba = wb.active
    aba.title = "Respostas"
    aba.append(COLUNAS_RESPOSTA)
    for r in respostas:
        aba.append([", ".join(r[c]) if c == "municipios" else r[c] for c in COLUNAS_RESPOSTA])
    for celula in aba[1]:
        celula.font = Font(name="Arial", bold=True)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    wb.save(caminho)
    return caminho


def ler_respostas(caminho: Path = RESPOSTAS) -> list[dict]:
    from openpyxl import load_workbook

    linhas = list(load_workbook(caminho, read_only=True)["Respostas"].iter_rows(values_only=True))
    respostas = []
    for valores in linhas[1:]:
        r = dict(zip(linhas[0], valores))
        r["sobre_energia"] = bool(r["sobre_energia"])
        r["municipios"] = [m.strip() for m in (r["municipios"] or "").split(",") if m.strip()]
        respostas.append(r)
    return respostas


def salvar_resultado(resultado: dict, caminho: Path = RESULTADO) -> Path:
    """A planilha do resultado: a resposta esperada e a da IA, lado a lado, e o resumo por dificuldade."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    resumo = wb.active
    resumo.title = "Resumo"
    resumo.append(["Dificuldade", "Certos", "Casos marcados"])
    for d, (c, n) in resultado["por_dificuldade"].items():
        resumo.append([d, c, n])
    resumo.append(["Total", resultado["certos"], resultado["marcados"]])
    resumo.append([])
    for nome, (c, n) in resultado["secundario"].items():
        resumo.append([f"Acerto secundário: {nome}", c, n])
    casos = wb.create_sheet("Casos")
    casos.append(["Caso", "Dificuldade", "Esperado: sobre energia", "Esperado: sentimento", "Esperado: tema",
                  "Esperado: município", "IA: sobre energia", "IA: sentimento", "IA: tema", "IA: municípios",
                  "IA: resumo", "IA: confiança", "Certo?", "Versão"])
    verde, vermelho = PatternFill("solid", fgColor="D9F2D0"), PatternFill("solid", fgColor="F8D0D0")
    for linha in resultado["linhas"]:
        e, ia = linha["esperado"], linha["ia"] or {}
        casos.append([linha["caso"], linha["dificuldade"], e["sobre"], e["sentimento"],
                      NOME_TEMA.get(e["tema"], ""), e["municipio"],
                      ("sim" if ia.get("sobre_energia") else "não") if ia else "", ia.get("sentimento", ""),
                      NOME_TEMA.get(ia.get("tema"), ""), ", ".join(ia.get("municipios", [])), ia.get("resumo", ""),
                      ia.get("confianca", ""), {True: "sim", False: "não", None: ""}[linha["certo"]],
                      ia.get("versao", "")])
        if linha["certo"] is not None:
            casos.cell(row=casos.max_row, column=13).fill = verde if linha["certo"] else vermelho
    for aba in (resumo, casos):
        for celula in aba[1]:
            celula.font = Font(name="Arial", bold=True)
        for coluna in aba.columns:
            aba.column_dimensions[coluna[0].column_letter].width = 18
    casos.column_dimensions["K"].width = 60
    for celula in casos["K"]:
        celula.alignment = Alignment(wrap_text=True, vertical="top")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    wb.save(caminho)
    return caminho
