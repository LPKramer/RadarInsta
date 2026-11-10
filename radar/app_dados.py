"""Dados do app (pasta mobile/): menções, contas monitoradas e o mapa dos municípios num JSON só."""
import json
import math
from pathlib import Path

from . import config, db, monitoramento, relatorio
from .mencoes import SETORES

# Malha municipal do IBGE (API de malhas v3, qualidade mínima): ~5 mil pontos, leve para o celular.
MALHA = Path(__file__).resolve().parent / "dados" / "malha_to.geojson"
SAIDA = config.RAIZ / "mobile" / "src" / "dados" / "radar.json"
LARGURA_MAPA = 200

SQL_CONTAS = """
SELECT c.username, c.nome, k.tipo, k.abrangencia, s.prioridade, ROUND(s.valor) AS score, c.seguidores, c.posts_30d,
       ROUND(100.0 * c.engajamento_medio / NULLIF(c.seguidores, 0), 2) AS engajamento_pct,
       (SELECT GROUP_CONCAT(cm.ibge_id) FROM conta_municipio cm
         WHERE cm.rede = c.rede AND cm.username = c.username AND cm.fonte = 'ia') AS municipios
FROM contas c
JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username
LEFT JOIN score s ON s.rede = c.rede AND s.username = c.username
WHERE c.rede = 'instagram'
ORDER BY c.seguidores DESC
"""


def _aneis(geometria: dict) -> list:
    if geometria["type"] == "Polygon":
        return geometria["coordinates"]
    return [anel for poligono in geometria["coordinates"] for anel in poligono]


def mapa(caminho: Path = MALHA, largura: int = LARGURA_MAPA) -> dict:
    """Projeta a malha num plano (equirretangular corrigida pela latitude média, norte para cima) e devolve um
    path SVG por código IBGE."""
    feicoes = json.loads(Path(caminho).read_text(encoding="utf-8"))["features"]
    pontos = [p for f in feicoes for anel in _aneis(f["geometry"]) for p in anel]
    oeste, leste = min(p[0] for p in pontos), max(p[0] for p in pontos)
    sul, norte = min(p[1] for p in pontos), max(p[1] for p in pontos)
    fator = math.cos(math.radians((norte + sul) / 2))
    escala = largura / ((leste - oeste) * fator)

    def path(geometria):
        return "".join("M" + "L".join(f"{(lon - oeste) * fator * escala:.1f},{(norte - lat) * escala:.1f}"
                                      for lon, lat in anel) + "Z" for anel in _aneis(geometria))

    return {"largura": largura, "altura": round((norte - sul) * escala, 1),
            "paths": {int(f["properties"]["codarea"]): path(f["geometry"]) for f in feicoes}}


def _valor(v):
    """NaN vira null e os tipos do numpy viram tipos do Python (o json não serializa int64)."""
    if hasattr(v, "item"):
        v = v.item()
    return None if isinstance(v, float) and math.isnan(v) else v


def _mencao(linha: dict, ids: dict[str, int]) -> dict:
    r = {k: _valor(v) for k, v in linha.items()}
    return {
        "id": r["post_id"], "data": r["data"], "conta": r["conta"], "rede": r["rede"], "formato": r["formato"],
        "tipoConta": r["tipo_conta"],
        "seguidores": r["seguidores"], "tema": r["tema"],
        "setor": r["setor"] if r["setor"] in SETORES else None,
        "sentimento": r["sentimento"], "alcance": r["alcance"],
        "municipios": [ids[n] for n in (r["municipios"] or "").split("; ") if n in ids],
        "citaEnergisa": r["cita_energisa"] == "sim", "curtidas": r["curtidas"], "comentarios": r["comentarios"],
        "visualizacoes": r["visualizacoes"], "interacoes": r["interacoes"], "engajamentoPct": r["engajamento_pct"],
        "indice": r["indice_vs_media_da_conta"], "repercutiram": r["contas_que_repercutiram"],
        "coberturaEstadual": r["teve_cobertura_estadual"] == "sim", "reacao": r["reacao"],
        "reacaoResumo": r["reacao_resumo"], "comentariosLidos": r["n_comentarios_lidos"], "resumo": r["resumo"],
        "legenda": r["legenda"], "link": r["link"],
    }


def _conta(linha: dict) -> dict:
    return {
        "username": linha["username"], "nome": linha["nome"], "tipo": linha["tipo"],
        "abrangencia": linha["abrangencia"], "prioridade": linha["prioridade"], "score": linha["score"],
        "seguidores": linha["seguidores"], "posts30d": linha["posts_30d"], "engajamentoPct": linha["engajamento_pct"],
        "municipios": [int(i) for i in (linha["municipios"] or "").split(",") if i],
    }


def exportar(con, caminho: Path | None = None) -> tuple[Path, dict]:
    caminho = Path(caminho or SAIDA)
    geo = mapa()
    linhas = [dict(l) for l in con.execute("SELECT ibge_id, nome, populacao FROM municipios ORDER BY nome")]
    municipios = [{"id": l["ibge_id"], "nome": l["nome"], "populacao": l["populacao"],
                   "d": geo["paths"].get(l["ibge_id"], "")} for l in linhas]
    ids = {l["nome"]: l["ibge_id"] for l in linhas}
    mencoes = [_mencao(r, ids) for r in relatorio.carregar(con).to_dict("records")]
    monitoradas = {c["username"] for c in monitoramento.monitoradas(con)}
    contas = [_conta(dict(l)) for l in con.execute(SQL_CONTAS) if l["username"] in monitoradas]
    dados = {
        "geradoEm": db.agora(), "coletadoEm": con.execute("SELECT MAX(medido_em) FROM posts").fetchone()[0],
        "mapa": {"largura": geo["largura"], "altura": geo["altura"]},
        "municipios": municipios, "mencoes": mencoes, "contas": contas,
    }
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return caminho, {"mencoes": len(mencoes), "contas": len(contas), "municipios": len(municipios)}
