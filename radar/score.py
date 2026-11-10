"""Etapa 5: score de relevância (0-100) e prioridade A/B/C."""
import math

from . import db

PESOS = {"alcance": 0.35, "engajamento": 0.15, "atividade": 0.15, "foco_to": 0.15, "confianca": 0.20}
LIMIAR_A, LIMIAR_B = 55, 40
# Contas não relevantes ainda recebem score (para ordenar), mas bem rebaixado.
FATOR_IRRELEVANTE = 0.2


def relevante(classif: dict, tem_municipio_ia: bool) -> bool:
    """Mesma regra de classificacao.SQL_RELEVANTE."""
    return (classif["tipo"] != "irrelevante" and bool(classif["publica_noticias_locais"])
            and (tem_municipio_ia or classif.get("abrangencia") == "estadual"))


def componentes(conta: dict, classif: dict, tem_municipio_ia: bool) -> dict:
    seguidores = conta.get("seguidores") or 0
    # 100 seguidores = 0, 10 mil = 0,5, 1 milhão = 1.
    alcance = min(max((math.log10(seguidores) - 2) / 4, 0), 1) if seguidores > 0 else 0
    taxa = (conta.get("engajamento_medio") or 0) / seguidores if seguidores else 0
    engajamento = min(taxa / 0.05, 1)                    # 5% de interação por post já é o teto
    atividade = min((conta.get("posts_30d") or 0) / 30, 1)  # um post por dia é o teto
    if classif.get("abrangencia") == "nacional":
        foco = 0.3
    else:
        foco = 1.0 if tem_municipio_ia or classif.get("abrangencia") == "estadual" else 0.4
    return {"alcance": alcance, "engajamento": engajamento, "atividade": atividade,
            "foco_to": foco, "confianca": classif.get("confianca") or 0}


def calcular(conta: dict, classif: dict, tem_municipio_ia: bool) -> tuple[float, str]:
    partes = componentes(conta, classif, tem_municipio_ia)
    valor = 100 * sum(PESOS[k] * v for k, v in partes.items())
    if not relevante(classif, tem_municipio_ia):
        return round(valor * FATOR_IRRELEVANTE, 1), "C"
    prioridade = "A" if valor >= LIMIAR_A else "B" if valor >= LIMIAR_B else "C"
    return round(valor, 1), prioridade


def pontuar(con) -> dict:
    linhas = con.execute(
        """SELECT c.*, k.tipo, k.publica_noticias_locais, k.abrangencia, k.confianca,
                  EXISTS (SELECT 1 FROM conta_municipio cm WHERE cm.rede = c.rede
                          AND cm.username = c.username AND cm.fonte = 'ia') AS tem_municipio_ia
           FROM contas c JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username"""
    ).fetchall()
    contagem = {"A": 0, "B": 0, "C": 0}
    for l in linhas:
        linha = dict(l)
        valor, prioridade = calcular(linha, linha, bool(linha["tem_municipio_ia"]))
        con.execute("INSERT OR REPLACE INTO score (rede, username, valor, prioridade, calculado_em) "
                    "VALUES (?, ?, ?, ?, ?)", (linha["rede"], linha["username"], valor, prioridade, db.agora()))
        contagem[prioridade] += 1
    con.commit()
    return contagem
