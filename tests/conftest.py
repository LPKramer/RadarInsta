import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from radar import classificacao as cl, db, municipios as mun, perfis


@pytest.fixture(autouse=True)
def _alertas_isolados(tmp_path, monkeypatch):
    """Nenhum teste escreve no data/alertas.txt de verdade."""
    from radar import vigia

    monkeypatch.setattr(vigia, "ARQUIVO_ALERTAS", tmp_path / "alertas.txt")


@pytest.fixture
def con():
    conexao = db.conectar(":memory:")
    mun.salvar(conexao, mun.ler_csv())
    yield conexao
    conexao.close()


@pytest.fixture
def municipios(con):
    return mun.carregar(con)


def post(dias_atras: int, curtidas: int = 100, comentarios: int = 10, legenda: str = "Notícia",
         codigo: str | None = None) -> dict:
    """Post como vem em `latestPosts`. Com `codigo`, ganha id, shortCode e URL (e passa a ser gravado em `posts`)."""
    data = datetime.now(timezone.utc) - timedelta(days=dias_atras)
    item = {"timestamp": data.isoformat().replace("+00:00", "Z"), "likesCount": curtidas,
            "commentsCount": comentarios, "caption": legenda}
    if codigo:
        item |= {"id": f"id-{codigo}", "shortCode": codigo, "url": f"https://www.instagram.com/p/{codigo}/"}
    return item


def perfil(username: str, **extra) -> dict:
    """Item no formato do instagram-search-scraper / instagram-profile-scraper da Apify."""
    base = {
        "id": "123", "username": username, "fullName": username.title(), "biography": "",
        "followersCount": 5000, "followsCount": 300, "postsCount": 800, "private": False,
        "verified": False, "isBusinessAccount": True, "businessCategoryName": None, "externalUrl": None,
        "latestPosts": [post(1), post(3), post(10)], "relatedProfiles": [],
    }
    return base | extra


class ApifyFalso:
    """Devolve itens fixos por ator e registra as entradas recebidas. `custo_por_run` simula o gasto."""

    def __init__(self, respostas: dict[str, list[dict]], custo_por_run: float = 0.0):
        self.respostas = respostas
        self.chamadas = []
        self.custo_por_run = custo_por_run
        self.gasto_usd = 0.0

    def rodar(self, ator, entrada, rotulo, max_usd=None):
        self.chamadas.append((ator, entrada))
        self.gasto_usd += self.custo_por_run
        return f"run-{len(self.chamadas)}", self.respostas.get(ator, [])


def mensagem(dados: dict | None = None, stop_reason: str = "end_turn"):
    texto = json.dumps(dados) if dados is not None else ""
    return SimpleNamespace(stop_reason=stop_reason, model="claude-haiku-5-5",
                           content=[SimpleNamespace(type="text", text=texto)])


def resposta_deepseek(conteudo, finish_reason="stop"):
    texto = json.dumps(conteudo) if isinstance(conteudo, dict) else conteudo
    return SimpleNamespace(model="deepseek-flash", choices=[
        SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=texto))])


def deepseek_falso(responder):
    """Cliente no formato do SDK da OpenAI. `responder(params)` devolve a resposta; as chamadas ficam registradas."""
    chamadas = []

    def create(**params):
        chamadas.append(params)
        return responder(params)

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), chamadas


def classificacao(**extra) -> dict:
    base = {"tipo": "portal_noticias", "publica_noticias_locais": True, "municipios": ["Gurupi"],
            "abrangencia": "municipal", "confianca": 0.9, "justificativa": "Publica notícias de Gurupi."}
    return base | extra


def conta(con, todos, username, posts, prioridade="A", seguidores=10_000, **classif):
    """Conta classificada e pontuada, com os posts dados. `todos` = lista de municípios do estado."""
    perfis.ingerir(con, perfil(username, followersCount=seguidores, latestPosts=posts), "busca", todos)
    cl.salvar(con, username, classificacao(**classif), todos, "m")
    con.execute("INSERT OR REPLACE INTO score (rede, username, valor, prioridade, calculado_em) "
                "VALUES ('instagram', ?, 60, ?, 'x')", (username, prioridade))
    con.commit()


def analise(**extra) -> dict:
    base = {"sobre_energia": True, "tema": "falta_energia", "sentimento": "negativo", "alcance": "local",
            "municipios": ["Gurupi"], "resumo": "Bairro ficou sem energia.", "confianca": 0.9}
    return base | extra
