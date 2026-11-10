"""Planilha de revisão humana (Excel) e reimportação das decisões da equipe."""
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from . import config, db, monitoramento

STATUS = ["pendente", "aprovada", "rejeitada"]
ABA_CONTAS = "Contas"
ABA_MUNICIPIOS = "Cobertura por município"
CORES_PRIORIDADE = {"A": "C6EFCE", "B": "FFEB9C", "C": "EDEDED"}

SQL_CONTAS = """
SELECT s.prioridade, s.valor AS score, COALESCE(r.status, 'pendente') AS status, r.nota,
       c.username, 'https://www.instagram.com/' || c.username || '/' AS link, c.nome, k.tipo,
       (SELECT GROUP_CONCAT(m.nome, '; ') FROM conta_municipio cm JOIN municipios m ON m.ibge_id = cm.ibge_id
         WHERE cm.rede = c.rede AND cm.username = c.username AND cm.fonte = 'ia') AS municipios_ia,
       (SELECT GROUP_CONCAT(DISTINCT m.nome) FROM conta_municipio cm JOIN municipios m ON m.ibge_id = cm.ibge_id
         WHERE cm.rede = c.rede AND cm.username = c.username AND cm.fonte != 'ia') AS pistas_municipio,
       (SELECT l.posicao FROM listas l WHERE l.rede = c.rede AND l.username = c.username
         AND l.lista = 'mais_seguidas_to') AS posicao_mais_seguidas,
       k.abrangencia, k.confianca, c.seguidores, c.posts_30d,
       ROUND(100.0 * c.engajamento_medio / NULLIF(c.seguidores, 0), 2) AS engajamento_pct,
       SUBSTR(c.ultimo_post_em, 1, 10) AS ultimo_post, c.categoria, c.bio, k.justificativa,
       c.origem, c.profundidade
FROM contas c
JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username
LEFT JOIN score s ON s.rede = c.rede AND s.username = c.username
LEFT JOIN revisao r ON r.rede = c.rede AND r.username = c.username
WHERE c.rede = 'instagram'
ORDER BY s.prioridade, s.valor DESC
"""

SQL_MUNICIPIOS = """
SELECT m.nome AS municipio, m.porte, m.populacao,
       COUNT(DISTINCT CASE WHEN s.prioridade = 'A' AND COALESCE(r.status, '') != 'rejeitada'
                           THEN s.username END) AS contas_A,
       COUNT(DISTINCT CASE WHEN s.prioridade = 'B' AND COALESCE(r.status, '') != 'rejeitada'
                           THEN s.username END) AS contas_B,
       COUNT(DISTINCT CASE WHEN r.status = 'aprovada' THEN r.username END) AS aprovadas
FROM municipios m
LEFT JOIN conta_municipio cm ON cm.ibge_id = m.ibge_id AND cm.fonte = 'ia'
LEFT JOIN score s ON s.rede = cm.rede AND s.username = cm.username
LEFT JOIN revisao r ON r.rede = cm.rede AND r.username = cm.username
GROUP BY m.ibge_id
ORDER BY contas_A + contas_B, m.populacao DESC
"""

LARGURAS = {"username": 24, "link": 38, "nome": 30, "tipo": 22, "municipios_ia": 28, "pistas_municipio": 28,
            "bio": 60, "justificativa": 60, "nota": 30, "categoria": 22}


def exportar(con, caminho: Path | None = None) -> tuple[Path, int]:
    caminho = Path(caminho or config.EXPORT_DIR / "contas_tocantins.xlsx")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    contas = pd.read_sql_query(SQL_CONTAS, con)
    monitoradas = {c["username"] for c in monitoramento.monitoradas(con)}
    contas.insert(3, "monitorada", contas["username"].isin(monitoradas).map({True: "sim", False: "não"}))
    cobertura = pd.read_sql_query(SQL_MUNICIPIOS, con)
    with pd.ExcelWriter(caminho, engine="openpyxl") as escritor:
        contas.to_excel(escritor, sheet_name=ABA_CONTAS, index=False)
        cobertura.to_excel(escritor, sheet_name=ABA_MUNICIPIOS, index=False)
    _formatar(caminho, len(contas))
    return caminho, len(contas)


def _formatar(caminho: Path, n_linhas: int) -> None:
    livro = load_workbook(caminho)
    for aba in livro.worksheets:
        aba.freeze_panes = "A2"
        aba.auto_filter.ref = aba.dimensions
        for celula in aba[1]:
            celula.font = Font(bold=True)
            aba.column_dimensions[celula.column_letter].width = LARGURAS.get(celula.value, 14)

    aba = livro[ABA_CONTAS]
    colunas = {c.value: c.column_letter for c in aba[1]}
    if n_linhas:
        validacao = DataValidation(type="list", formula1=f'"{",".join(STATUS)}"', allow_blank=False)
        aba.add_data_validation(validacao)
        validacao.add(f"{colunas['status']}2:{colunas['status']}{n_linhas + 1}")
    for linha in range(2, n_linhas + 2):
        link = aba[f"{colunas['link']}{linha}"]
        link.hyperlink, link.style = link.value, "Hyperlink"
        prioridade = aba[f"{colunas['prioridade']}{linha}"]
        if prioridade.value in CORES_PRIORIDADE:
            prioridade.fill = PatternFill("solid", fgColor=CORES_PRIORIDADE[prioridade.value])
    livro.save(caminho)


def importar_revisao(con, caminho: Path) -> dict:
    """Lê status e nota da aba Contas e grava em `revisao`. Status inválidos são ignorados e contados."""
    planilha = pd.read_excel(caminho, sheet_name=ABA_CONTAS, dtype={"username": str, "status": str, "nota": str})
    resumo = {s: 0 for s in STATUS} | {"invalidas": 0}
    for _, linha in planilha.iterrows():
        status = str(linha.get("status") or "").strip().lower()
        nota = linha.get("nota")
        nota = None if pd.isna(nota) else str(nota)
        if status not in STATUS or pd.isna(linha.get("username")):
            resumo["invalidas"] += 1
            continue
        con.execute(
            "INSERT INTO revisao (rede, username, status, nota, revisada_em) VALUES ('instagram', ?, ?, ?, ?) "
            "ON CONFLICT (rede, username) DO UPDATE SET status = excluded.status, nota = excluded.nota, "
            "revisada_em = CASE WHEN revisao.status = excluded.status AND revisao.nota IS excluded.nota "
            "THEN revisao.revisada_em ELSE excluded.revisada_em END",
            (linha["username"].strip().lower(), status, nota, db.agora() if status != "pendente" else None),
        )
        resumo[status] += 1
    con.commit()
    return resumo
