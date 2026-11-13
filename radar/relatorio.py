"""Fase 2, relatório: menções à energia/Energisa com engajamento, sentimento, tema e alcance do impacto."""
from datetime import timedelta
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

from . import config

ABA_MENCOES = "Menções"
CORES_SENTIMENTO = {"negativo": "FFC7CE", "positivo": "C6EFCE", "neutro": "EDEDED"}
JANELA_REPERCUSSAO = timedelta(days=3)

SQL = """
SELECT p.post_id, p.publicado_em, p.rede,
       CASE WHEN p.rede = 'web' THEN COALESCE(c.nome, p.username) ELSE p.username END AS conta,
       p.tipo AS formato, p.arquivo,
       COALESCE(k.tipo, CASE WHEN p.rede = 'web' THEN 'site_noticias' END) AS tipo_conta, k.abrangencia AS abrangencia_conta,
       c.seguidores, c.engajamento_medio, x.tema, x.setor, x.sentimento, x.alcance, x.cita_energisa,
       (SELECT GROUP_CONCAT(m.nome, '; ') FROM mencao_municipio mm JOIN municipios m ON m.ibge_id = mm.ibge_id
         WHERE mm.rede = x.rede AND mm.post_id = x.post_id) AS municipios,
       p.curtidas, p.comentarios, p.interacoes_estimadas, p.visualizacoes, p.medido_em, x.reacao, x.reacao_resumo, x.n_comentarios_lidos,
       (SELECT COUNT(*) FROM comentario_analise a JOIN comentarios k ON k.rede = a.rede
          AND k.comentario_id = a.comentario_id WHERE k.rede = x.rede AND k.post_id = x.post_id) AS comentarios_analisados,
       (SELECT SUM(a.sentimento = 'negativo') FROM comentario_analise a JOIN comentarios k ON k.rede = a.rede
          AND k.comentario_id = a.comentario_id WHERE k.rede = x.rede AND k.post_id = x.post_id) AS comentarios_negativos,
       (SELECT COUNT(*) FROM comentario_analise a JOIN comentarios k ON k.rede = a.rede
          AND k.comentario_id = a.comentario_id WHERE k.rede = x.rede AND k.post_id = x.post_id
          AND a.relato IN ('falta_energia', 'oscilacao')) AS relatos_de_falta,
       (SELECT GROUP_CONCAT(DISTINCT a.local) FROM comentario_analise a JOIN comentarios k ON k.rede = a.rede
          AND k.comentario_id = a.comentario_id WHERE k.rede = x.rede AND k.post_id = x.post_id
          AND a.relato IN ('falta_energia', 'oscilacao') AND a.local IS NOT NULL) AS locais_dos_relatos,
       (SELECT COALESCE(i.cidade || '-', '') || COALESCE(i.uf, '?') || ' (' || CAST(ROUND(i.confianca, 2) AS TEXT)
               || ')' FROM investigacoes i WHERE i.rede = p.rede AND i.username = p.username) AS local_provavel_da_conta,
       x.resumo, x.confianca, p.legenda, p.url AS link
FROM mencoes x
JOIN posts p ON p.rede = x.rede AND p.post_id = x.post_id
JOIN contas c ON c.rede = p.rede AND c.username = p.username
LEFT JOIN classificacoes k ON k.rede = p.rede AND k.username = p.username
WHERE x.sobre_energia = 1
"""

COLUNAS = ["data", "conta", "rede", "formato", "tipo_conta", "abrangencia_conta", "seguidores", "tema", "setor", "sentimento",
           "alcance",
           "municipios", "cita_energisa", "curtidas", "comentarios", "visualizacoes", "interacoes",
           "interacoes_estimadas", "engajamento_pct",
           "indice_vs_media_da_conta", "contas_que_repercutiram", "teve_cobertura_estadual", "fato_id", "fato_posts",
           "fato_interacoes", "horas_ate_medicao",
           "reacao", "reacao_resumo", "n_comentarios_lidos", "pct_comentarios_negativos", "relatos_de_falta",
           "locais_dos_relatos", "local_provavel_da_conta", "resumo", "confianca", "legenda", "fala_no_video", "link",
           "arquivo", "post_id"]
LARGURAS = {"conta": 24, "tipo_conta": 22, "tema": 22, "setor": 20, "municipios": 28, "reacao_resumo": 60, "resumo": 60,
            "legenda": 60, "fala_no_video": 60, "link": 40, "locais_dos_relatos": 30, "local_provavel_da_conta": 26, "texto": 70,
            "local": 22, "titulo": 60, "contas": 40, "links": 60, "primeira": 17, "ultima": 17}

# Aba "Comentários": a voz da população, comentário por comentário, sem quem comentou.
ABA_COMENTARIOS = "Comentários"
SQL_COMENTARIOS = """
SELECT k.publicado_em AS data, p.username AS conta, k.texto, k.curtidas, a.sentimento, a.relato, a.local,
       m.nome AS municipio, p.url AS link
FROM comentarios k
JOIN posts p ON p.rede = k.rede AND p.post_id = k.post_id
LEFT JOIN comentario_analise a ON a.rede = k.rede AND a.comentario_id = k.comentario_id
LEFT JOIN municipios m ON m.ibge_id = a.ibge_id
WHERE k.texto IS NOT NULL
"""


def carregar(con, desde: str | None = None) -> pd.DataFrame:
    sql, args = SQL, []
    if desde:
        sql += " AND p.publicado_em >= ?"
        args.append(desde)
    df = pd.read_sql_query(sql + " ORDER BY p.publicado_em DESC", con, params=args)
    if df.empty:
        return pd.DataFrame(columns=COLUNAS)

    publicado = pd.to_datetime(df["publicado_em"], utc=True, errors="coerce")
    df["data"] = publicado.dt.strftime("%Y-%m-%d")
    df["municipios"] = df["municipios"].fillna("")
    df["setor"] = df["setor"].fillna("(sem setor)")
    # O story entra pela estimativa (estimativa.py): a mediana dos posts da conta, que também vem à parte.
    df["interacoes"] = (df["curtidas"].fillna(0) + df["comentarios"].fillna(0)
                        + df["interacoes_estimadas"].fillna(0))
    medido_de_verdade = df["formato"] != "Story"  # a taxa e o índice de uma estimativa pela mediana não diriam nada
    df["engajamento_pct"] = (100 * df["interacoes"] / df["seguidores"].where(df["seguidores"] > 0)
                             ).round(2).where(medido_de_verdade)
    # >1 = o post rendeu mais que a média dos posts recentes da própria conta.
    df["indice_vs_media_da_conta"] = (df["interacoes"] / df["engajamento_medio"].where(df["engajamento_medio"] > 0)
                                      ).round(2).where(medido_de_verdade)
    medido = pd.to_datetime(df["medido_em"], utc=True, errors="coerce")
    df["horas_ate_medicao"] = ((medido - publicado).dt.total_seconds() / 3600).round(0)
    df["cita_energisa"] = df["cita_energisa"].map({1: "sim", 0: "não"})
    df["pct_comentarios_negativos"] = (100 * df["comentarios_negativos"].fillna(0)
                                       / df["comentarios_analisados"].where(df["comentarios_analisados"] > 0)).round(0)
    contas, estadual = repercussao(df.assign(_quando=publicado))
    df["fato_id"], df["fato_posts"], df["fato_interacoes"] = _fatos_dos_posts(con, df)
    # Com o agrupamento da mesma notícia (fatos.py), a repercussão é a dele; sem, a conta antiga, por tema e lugar.
    perfis_do_fato = _perfis_dos_fatos(con, df["fato_id"].dropna().unique())
    estadual_do_fato = df.groupby("fato_id")["abrangencia_conta"].agg(lambda s: (s == "estadual").any()).to_dict()
    tem = [pd.notna(f) for f in df["fato_id"]]
    df["contas_que_repercutiram"] = [perfis_do_fato[f] if t else c for f, t, c in zip(df["fato_id"], tem, contas)]
    df["teve_cobertura_estadual"] = ["sim" if (estadual_do_fato.get(f) if t else e) else "não"
                                     for f, t, e in zip(df["fato_id"], tem, estadual)]
    df["fala_no_video"] = _falas(con, df)
    return df[COLUNAS]


def _falas(con, df: pd.DataFrame) -> list[str]:
    """Os trechos da fala sobre energia nos vídeos das rádios (transcricao.py). O painel abre o banco só para leitura:
    ele pode ser de antes da tabela."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'transcricoes'").fetchone() is None:
        return [""] * len(df)
    falas = dict(con.execute("SELECT post_id, trechos FROM transcricoes WHERE rede = 'instagram' AND energia = 1"
                             ).fetchall())
    return [falas.get(p, "") if r == "instagram" else "" for p, r in zip(df["post_id"], df["rede"])]


def _tem_fatos(con) -> bool:
    """O painel abre o banco só para leitura, sem migração: o banco pode ser de antes da tabela."""
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'fato_mencao'").fetchone() is not None


def _fatos_dos_posts(con, df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    vazio = pd.Series([pd.NA] * len(df), index=df.index, dtype="Int64")
    if not _tem_fatos(con):
        return vazio, vazio, vazio
    ligacoes = pd.read_sql_query(
        "SELECT f.post_id, f.rede, f.fato_id, COUNT(*) OVER (PARTITION BY f.fato_id) AS fato_posts, "
        "SUM(COALESCE(p.curtidas, 0) + COALESCE(p.comentarios, 0) + COALESCE(p.interacoes_estimadas, 0)) "
        "OVER (PARTITION BY f.fato_id) AS fato_interacoes "
        "FROM fato_mencao f JOIN posts p ON p.rede = f.rede AND p.post_id = f.post_id", con)
    junto = df[["post_id", "rede"]].merge(ligacoes, on=["post_id", "rede"], how="left")
    junto.index = df.index
    # Só faz sentido falar em "mesma notícia" quando ela saiu em mais de um post.
    sozinho = junto["fato_posts"].fillna(0) < 2
    return tuple(junto[c].mask(sozinho).astype("Int64") for c in ("fato_id", "fato_posts", "fato_interacoes"))


def _perfis_dos_fatos(con, ids) -> dict:
    from . import fatos

    return {i: fatos.resumo(con, int(i))["perfis"] for i in ids}


# Aba "Mesma notícia": um agrupamento por linha, com as interações somadas e os links (fatos.py).
ABA_FATOS = "Mesma notícia"


def mesma_noticia(con, desde: str | None = None) -> pd.DataFrame:
    from . import fatos

    colunas = ["fato_id", "titulo", "primeira", "ultima", "horas_de_cobertura", "perfis", "posts", "redes", "contas",
               "tema", "sentimento", "municipios", "curtidas", "comentarios", "interacoes", "visualizacoes", "links"]
    if not _tem_fatos(con):
        return pd.DataFrame(columns=colunas)
    sql, args = "SELECT fato_id FROM fato_mencao f JOIN fatos a ON a.id = f.fato_id", []
    if desde:
        sql += " WHERE a.ultima_em >= ?"
        args.append(desde)
    linhas = []
    for (fato_id,) in con.execute(sql + " GROUP BY fato_id HAVING COUNT(*) >= 2 ORDER BY a.primeira_em DESC", args):
        r = fatos.resumo(con, fato_id)
        detalhes = con.execute(
            "SELECT p.username, x.tema, x.sentimento, (SELECT GROUP_CONCAT(m.nome, '; ') FROM mencao_municipio mm "
            "JOIN municipios m ON m.ibge_id = mm.ibge_id WHERE mm.rede = x.rede AND mm.post_id = x.post_id) "
            "FROM fato_mencao f JOIN mencoes x ON x.rede = f.rede AND x.post_id = f.post_id "
            "JOIN posts p ON p.rede = f.rede AND p.post_id = f.post_id WHERE f.fato_id = ?", (fato_id,)).fetchall()
        municipios = sorted({m for d in detalhes for m in (d[3] or "").split("; ") if m})
        inicio, fim = (pd.Timestamp(r[k]["data"]) for k in ("primeira", "ultima"))
        linhas.append({
            "fato_id": fato_id, "titulo": r["titulo"], "primeira": r["primeira"]["data"][:16].replace("T", " "),
            "ultima": r["ultima"]["data"][:16].replace("T", " "),
            "horas_de_cobertura": round((fim - inicio).total_seconds() / 3600),
            "perfis": r["perfis"], "posts": r["posts"], "redes": ", ".join(r["redes"]),
            "contas": ", ".join(dict.fromkeys(d[0] for d in detalhes)),
            "tema": pd.Series([d[1] for d in detalhes]).mode().iat[0],
            "sentimento": pd.Series([d[2] for d in detalhes]).mode().iat[0],
            "municipios": "; ".join(municipios), "curtidas": r["curtidas"], "comentarios": r["comentarios"],
            "interacoes": r["interacoes"], "visualizacoes": r["visualizacoes"], "links": "\n".join(r["links"]),
        })
    return pd.DataFrame(linhas, columns=colunas)


def repercussao(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Para cada menção: quantas contas falaram do mesmo tema, no mesmo município (ou, sem município, com o mesmo
    alcance estadual/nacional), até 3 dias antes ou depois; e se alguma delas tem cobertura estadual."""
    linhas = df.to_dict("records")
    municipios = [set(filter(None, r["municipios"].split("; "))) for r in linhas]
    contas, estadual = [], []
    for i, r in enumerate(linhas):
        grupo = set()
        for j, s in enumerate(linhas):
            if s["tema"] != r["tema"] or pd.isna(r["_quando"]) or pd.isna(s["_quando"]):
                continue
            if abs(s["_quando"] - r["_quando"]) > JANELA_REPERCUSSAO:
                continue
            mesmo_lugar = (municipios[i] & municipios[j] if municipios[i] and municipios[j]
                           else r["alcance"] == s["alcance"] and r["alcance"] in ("estadual", "nacional"))
            if mesmo_lugar or i == j:
                grupo.add(j)
        contas.append(len({linhas[j]["conta"] for j in grupo}))
        estadual.append(any(linhas[j]["abrangencia_conta"] == "estadual" for j in grupo))
    return pd.Series(contas, index=df.index), pd.Series(estadual, index=df.index)


def resumo_por(df: pd.DataFrame, chave: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[chave])
    base = df.explode(chave) if chave == "municipio" else df
    tabela = base.groupby(chave).agg(
        mencoes=("conta", "size"), contas=("conta", "nunique"),
        negativas=("sentimento", lambda s: (s == "negativo").sum()),
        positivas=("sentimento", lambda s: (s == "positivo").sum()),
        neutras=("sentimento", lambda s: (s == "neutro").sum()),
        interacoes=("interacoes", "sum"), engajamento_pct_medio=("engajamento_pct", "mean"),
        so_local=("alcance", lambda s: (s == "local").sum()),
    )
    tabela["pct_negativas"] = (100 * tabela["negativas"] / tabela["mencoes"]).round(0)
    tabela["engajamento_pct_medio"] = tabela["engajamento_pct_medio"].round(2)
    return tabela.sort_values(["mencoes", "interacoes"], ascending=False).reset_index()


def exportar(con, caminho: Path | None = None, desde: str | None = None) -> tuple[Path, int]:
    caminho = Path(caminho or config.EXPORT_DIR / "mencoes_energisa.xlsx")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df = carregar(con, desde)
    por_municipio = df.assign(municipio=df["municipios"].str.split("; "))
    por_municipio["municipio"] = por_municipio["municipio"].map(lambda l: [m for m in l if m] or ["(não identificado)"])
    cruzado = (pd.crosstab(df["sentimento"], df["alcance"], margins=True, margins_name="total")
               if not df.empty else pd.DataFrame())
    with pd.ExcelWriter(caminho, engine="openpyxl") as escritor:
        df.to_excel(escritor, sheet_name=ABA_MENCOES, index=False)
        resumo_por(df, "tema").to_excel(escritor, sheet_name="Por tema", index=False)
        resumo_por(df, "setor").to_excel(escritor, sheet_name="Por setor", index=False)
        resumo_por(por_municipio, "municipio").to_excel(escritor, sheet_name="Por município", index=False)
        cruzado.to_excel(escritor, sheet_name="Sentimento x alcance")
        comentarios(con, desde).to_excel(escritor, sheet_name=ABA_COMENTARIOS, index=False)
        mesma_noticia(con, desde).to_excel(escritor, sheet_name=ABA_FATOS, index=False)
    _formatar(caminho, len(df))
    return caminho, len(df)


def comentarios(con, desde: str | None = None) -> pd.DataFrame:
    sql, args = SQL_COMENTARIOS, []
    if desde:
        sql += " AND p.publicado_em >= ?"
        args.append(desde)
    df = pd.read_sql_query(sql + " ORDER BY k.publicado_em DESC", con, params=args)
    df["data"] = df["data"].str[:10]
    return df


def _formatar(caminho: Path, n_linhas: int) -> None:
    livro = load_workbook(caminho)
    for aba in livro.worksheets:
        aba.freeze_panes = "A2"
        aba.auto_filter.ref = aba.dimensions
        for celula in aba[1]:
            celula.font = Font(bold=True)
            aba.column_dimensions[celula.column_letter].width = LARGURAS.get(celula.value, 14)
    aba = livro[ABA_MENCOES]
    colunas = {c.value: c.column_letter for c in aba[1]}
    for linha in range(2, n_linhas + 2):
        link = aba[f"{colunas['link']}{linha}"]
        if link.value:
            link.hyperlink = link.value
            link.style = "Hyperlink"
        sentimento = aba[f"{colunas['sentimento']}{linha}"]
        if sentimento.value in CORES_SENTIMENTO:
            sentimento.fill = PatternFill("solid", fgColor=CORES_SENTIMENTO[sentimento.value])
    aba = livro[ABA_COMENTARIOS]
    colunas = {c.value: c.column_letter for c in aba[1]}
    for linha in range(2, aba.max_row + 1):
        sentimento = aba[f"{colunas['sentimento']}{linha}"]
        if sentimento.value in CORES_SENTIMENTO:
            sentimento.fill = PatternFill("solid", fgColor=CORES_SENTIMENTO[sentimento.value])
    livro.save(caminho)
