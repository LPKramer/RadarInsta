"""Esquema SQLite e funções de gravação. A coluna `rede` deixa a base pronta para X, Facebook e TikTok."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config

ESQUEMA = """
CREATE TABLE IF NOT EXISTS municipios (
    ibge_id         INTEGER PRIMARY KEY,
    nome            TEXT NOT NULL,
    nome_norm       TEXT NOT NULL,
    nome_curto      TEXT NOT NULL,
    variantes       TEXT NOT NULL,          -- JSON: formas normalizadas usadas no casamento
    microrregiao    TEXT,
    regiao_imediata TEXT,
    populacao       INTEGER,
    porte           TEXT                    -- T1 (>40 mil), T2 (10-40 mil), T3 (<10 mil)
);

CREATE TABLE IF NOT EXISTS buscas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fonte         TEXT NOT NULL,            -- busca | hashtag | google
    termo         TEXT NOT NULL,
    ibge_id       INTEGER,
    executada_em  TEXT NOT NULL,
    apify_run_id  TEXT,
    n_resultados  INTEGER,
    UNIQUE (fonte, termo)
);

CREATE TABLE IF NOT EXISTS contas (
    rede              TEXT NOT NULL DEFAULT 'instagram',
    username          TEXT NOT NULL,
    ig_id             TEXT,
    nome              TEXT,
    bio               TEXT,
    seguidores        INTEGER,
    seguindo          INTEGER,
    n_posts           INTEGER,
    privada           INTEGER,
    verificada        INTEGER,
    business          INTEGER,
    categoria         TEXT,
    url_externa       TEXT,
    ultimo_post_em    TEXT,
    engajamento_medio REAL,                 -- média de curtidas + comentários dos últimos posts
    posts_30d         INTEGER,
    posts_recentes    TEXT,                 -- JSON com as últimas legendas (entrada da IA)
    completo          INTEGER NOT NULL DEFAULT 0,   -- 1 quando já temos o perfil completo
    descartada        TEXT,                 -- motivo do pré-filtro; NULL se passou
    origem            TEXT NOT NULL,        -- busca | hashtag | google | relacionada | manual
    profundidade      INTEGER NOT NULL DEFAULT 0,   -- passos de bola de neve desde a busca
    descoberta_em     TEXT NOT NULL,
    atualizada_em     TEXT,
    PRIMARY KEY (rede, username)
);

CREATE TABLE IF NOT EXISTS conta_municipio (
    rede      TEXT NOT NULL,
    username  TEXT NOT NULL,
    ibge_id   INTEGER NOT NULL,
    fonte     TEXT NOT NULL,                -- busca | bio | ia
    PRIMARY KEY (rede, username, ibge_id, fonte)
);

CREATE TABLE IF NOT EXISTS classificacoes (
    rede                    TEXT NOT NULL,
    username                TEXT NOT NULL,
    tipo                    TEXT NOT NULL,
    publica_noticias_locais INTEGER NOT NULL,
    abrangencia             TEXT,
    confianca               REAL,
    justificativa           TEXT,
    modelo                  TEXT,
    classificada_em         TEXT NOT NULL,
    PRIMARY KEY (rede, username)
);

CREATE TABLE IF NOT EXISTS historico (
    rede        TEXT NOT NULL,
    username    TEXT NOT NULL,
    desde       TEXT NOT NULL,              -- data mais antiga coberta pela coleta de histórico (AAAA-MM-DD)
    n_posts     INTEGER,
    coletado_em TEXT NOT NULL,
    PRIMARY KEY (rede, username)
);

CREATE TABLE IF NOT EXISTS listas (
    rede          TEXT NOT NULL,
    username      TEXT NOT NULL,
    lista         TEXT NOT NULL,            -- mais_seguidas_to: pessoas e criadores que completam a base monitorada
    posicao       INTEGER,
    seguidores    INTEGER,
    atualizada_em TEXT NOT NULL,
    PRIMARY KEY (rede, username, lista)
);

CREATE TABLE IF NOT EXISTS tarefas (
    rodada       TEXT NOT NULL,             -- ex.: 2026-11-04-stories
    username     TEXT NOT NULL,
    estado       TEXT NOT NULL,             -- pendente | em_andamento | feita
    conta        TEXT,                      -- conta do Instagram (perfil do navegador) que pegou o lote
    iniciada_em  TEXT,
    terminada_em TEXT,
    PRIMARY KEY (rodada, username)
);

CREATE TABLE IF NOT EXISTS reservas (
    rede         TEXT NOT NULL,
    username     TEXT NOT NULL,
    conta        TEXT NOT NULL,             -- conta do Instagram (perfil do navegador) que está visitando agora
    reservada_em TEXT NOT NULL,
    PRIMARY KEY (rede, username)
);

CREATE TABLE IF NOT EXISTS visitas (
    rede      TEXT NOT NULL,
    username  TEXT NOT NULL,
    ultima_em TEXT NOT NULL,                -- última vez que o monitoramento pelo navegador leu a conta
    PRIMARY KEY (rede, username)
);

CREATE TABLE IF NOT EXISTS semelhantes (
    rede     TEXT NOT NULL,
    username TEXT NOT NULL,
    n        INTEGER,                       -- contas parecidas que o Instagram sugeriu
    lido_em  TEXT NOT NULL,
    PRIMARY KEY (rede, username)
);

CREATE TABLE IF NOT EXISTS relacionadas (
    rede        TEXT NOT NULL,
    origem      TEXT NOT NULL,
    relacionada TEXT NOT NULL,
    nome        TEXT,                       -- nome do perfil relacionado, como o Instagram mostra
    PRIMARY KEY (rede, origem, relacionada)
);

CREATE TABLE IF NOT EXISTS revisao (
    rede        TEXT NOT NULL,
    username    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pendente',   -- pendente | aprovada | rejeitada
    nota        TEXT,
    revisada_em TEXT,
    PRIMARY KEY (rede, username)
);

CREATE TABLE IF NOT EXISTS score (
    rede         TEXT NOT NULL,
    username     TEXT NOT NULL,
    valor        REAL NOT NULL,
    prioridade   TEXT NOT NULL,                     -- A | B | C
    calculado_em TEXT NOT NULL,
    PRIMARY KEY (rede, username)
);

CREATE TABLE IF NOT EXISTS lotes_ia (
    id            TEXT PRIMARY KEY,                 -- id do Message Batch
    criado_em     TEXT NOT NULL,
    n_requisicoes INTEGER NOT NULL,
    status        TEXT NOT NULL                     -- enviado | processado
);

-- Fase 2: posts das contas e menções à energia elétrica / Energisa.
CREATE TABLE IF NOT EXISTS posts (
    rede          TEXT NOT NULL,
    post_id       TEXT NOT NULL,
    username      TEXT NOT NULL,
    shortcode     TEXT,
    url           TEXT,
    tipo          TEXT,                     -- Image | Video | Sidecar
    publicado_em  TEXT,
    legenda       TEXT,
    curtidas      INTEGER,                  -- NULL quando o perfil esconde as curtidas
    comentarios   INTEGER,
    visualizacoes INTEGER,
    local         TEXT,
    medido_em     TEXT NOT NULL,            -- última coleta (o engajamento cresce nos primeiros dias)
    PRIMARY KEY (rede, post_id)
);
CREATE INDEX IF NOT EXISTS posts_por_conta ON posts (rede, username);
CREATE INDEX IF NOT EXISTS posts_por_shortcode ON posts (shortcode);

CREATE TABLE IF NOT EXISTS mencoes (
    rede            TEXT NOT NULL,
    post_id         TEXT NOT NULL,
    sobre_energia   INTEGER NOT NULL,       -- 0 quando a palavra-chave apareceu por acaso
    cita_energisa   INTEGER NOT NULL,
    tema            TEXT,
    setor           TEXT,                   -- área da Energisa mais envolvida (mencoes.SETORES)
    sentimento      TEXT,                   -- efeito do post na imagem da Energisa
    alcance         TEXT,                   -- local | regional | estadual | nacional
    resumo          TEXT,
    confianca       REAL,
    modelo          TEXT,
    analisada_em    TEXT NOT NULL,
    reacao          TEXT,                   -- reação nos comentários: positiva | negativa | mista | neutra
    reacao_resumo   TEXT,
    n_comentarios_lidos INTEGER,
    PRIMARY KEY (rede, post_id)
);

CREATE TABLE IF NOT EXISTS mencao_municipio (
    rede      TEXT NOT NULL,
    post_id   TEXT NOT NULL,
    ibge_id   INTEGER NOT NULL,
    PRIMARY KEY (rede, post_id, ibge_id)
);

-- Triagem dos stories que não passaram no filtro de palavras: a IA diz, em lote, quais falam de energia (triagem.py).
CREATE TABLE IF NOT EXISTS triagem (
    rede       TEXT NOT NULL,
    post_id    TEXT NOT NULL,
    energia    INTEGER NOT NULL,
    modelo     TEXT,
    triado_em  TEXT NOT NULL,
    PRIMARY KEY (rede, post_id)
);

-- Posts achados pela busca por palavra-chave do Instagram (busca_posts.py), de qualquer conta, e o termo que os achou.
CREATE TABLE IF NOT EXISTS posts_busca (
    rede      TEXT NOT NULL,
    post_id   TEXT NOT NULL,
    termo     TEXT NOT NULL,
    achado_em TEXT NOT NULL,
    PRIMARY KEY (rede, post_id)
);

-- Sentimento e relato de cada comentário, pela DeepSeek (comentarios.py). Sem o autor, como em `comentarios`.
CREATE TABLE IF NOT EXISTS comentario_analise (
    rede           TEXT NOT NULL,
    comentario_id  TEXT NOT NULL,
    sentimento     TEXT,                    -- sobre a Energisa e o serviço: negativo | positivo | neutro
    relato         TEXT,                    -- falta_energia | oscilacao | conta_tarifa | atendimento | elogio | outro
    local          TEXT,                    -- bairro, setor ou cidade citado no comentário
    ibge_id        INTEGER,
    modelo         TEXT,
    analisado_em   TEXT NOT NULL,
    PRIMARY KEY (rede, comentario_id)
);

-- Alertas de relatos nos comentários já dados: um por lugar por dia (comentarios.alertas_de_relatos).
CREATE TABLE IF NOT EXISTS alertas_relatos (
    dia    TEXT NOT NULL,
    chave  TEXT NOT NULL,                   -- local normalizado | código IBGE
    n      INTEGER,
    PRIMARY KEY (dia, chave)
);

-- Localização provável de quem posta, pela DeepSeek (investigacao.py), para a busca de posts de qualquer conta.
CREATE TABLE IF NOT EXISTS investigacoes (
    rede            TEXT NOT NULL,
    username        TEXT NOT NULL,
    cidade          TEXT,
    uf              TEXT,
    ibge_id         INTEGER,
    do_to           INTEGER NOT NULL,
    confianca       REAL,
    evidencias      TEXT,
    etapa           INTEGER NOT NULL,       -- 1: só com o que veio na busca; 2: com o perfil lido
    modelo          TEXT,
    investigada_em  TEXT NOT NULL,
    PRIMARY KEY (rede, username)
);

-- Só o texto: o autor de um comentário é pessoa comum e não interessa ao radar.
CREATE TABLE IF NOT EXISTS comentarios (
    rede           TEXT NOT NULL,
    comentario_id  TEXT NOT NULL,
    post_id        TEXT NOT NULL,
    texto          TEXT,
    curtidas       INTEGER,
    publicado_em   TEXT,
    PRIMARY KEY (rede, comentario_id)
);

-- A mesma notícia (fatos.py): as menções que tratam do mesmo fato, em vários perfis, redes e sites.
CREATE TABLE IF NOT EXISTS fatos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo         TEXT,                    -- o resumo da primeira menção
    primeira_em    TEXT,
    ultima_em      TEXT,
    criado_em      TEXT NOT NULL,
    atualizado_em  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fato_mencao (
    rede       TEXT NOT NULL,
    post_id    TEXT NOT NULL,
    fato_id    INTEGER NOT NULL,
    como       TEXT NOT NULL,               -- novo | link | ia | manual
    modelo     TEXT,
    ligado_em  TEXT NOT NULL,
    PRIMARY KEY (rede, post_id)
);
CREATE INDEX IF NOT EXISTS fato_mencao_por_fato ON fato_mencao (fato_id);
-- A revisão dos agrupamentos (fatos.revisar): a decisão sobre cada par (a < b), para não perguntar de novo.
CREATE TABLE IF NOT EXISTS fato_pares (
    a            INTEGER NOT NULL,
    b            INTEGER NOT NULL,
    mesmo        INTEGER NOT NULL,          -- 1: juntados; 0: fatos diferentes
    como         TEXT NOT NULL,             -- texto | ia
    modelo       TEXT,
    decidido_em  TEXT NOT NULL,
    PRIMARY KEY (a, b)
);

-- Rádios do TO (radios.py): a classificação da DeepSeek das contas com cara de rádio, para o usuário aprovar.
CREATE TABLE IF NOT EXISTS radios (
    rede             TEXT NOT NULL,
    username         TEXT NOT NULL,
    categoria        TEXT NOT NULL,         -- emissora | programa | radialista | nao
    cidade           TEXT,
    uf               TEXT,
    ibge_id          INTEGER,
    do_to            INTEGER NOT NULL,
    confianca        REAL,
    publica_videos   INTEGER,
    evidencias       TEXT,
    modelo           TEXT,
    classificada_em  TEXT NOT NULL,
    PRIMARY KEY (rede, username)
);

-- Vídeos das rádios (transcricao.py): a fila e o resultado. No banco ficam só os trechos que falam de energia, com
-- os telefones mascarados; a transcrição completa fica fora do repositório (RADAR_MIDIA_DIR). O endereço assinado
-- do áudio é apagado depois de usado.
CREATE TABLE IF NOT EXISTS transcricoes (
    rede          TEXT NOT NULL,
    post_id       TEXT NOT NULL,
    username      TEXT NOT NULL,
    estado        TEXT NOT NULL,            -- pendente | feita | sem_fala | expirado | erro
    midia_url     TEXT,
    midia_expira  TEXT,
    duracao       REAL,                     -- segundos do vídeo
    segundos      REAL,                     -- segundos transcritos (até 15 minutos)
    trechos       TEXT,
    energia       INTEGER,
    modelo        TEXT,
    cpu_segundos  REAL,
    tentativas    INTEGER NOT NULL DEFAULT 0,
    erro          TEXT,
    criado_em     TEXT NOT NULL,
    feito_em      TEXT,
    PRIMARY KEY (rede, post_id)
);
CREATE INDEX IF NOT EXISTS transcricoes_por_estado ON transcricoes (estado);

-- Sites de notícia lidos direto pelo feed (sites.py): portais, jornais, investigativos e as páginas das rádios.
CREATE TABLE IF NOT EXISTS sites (
    dominio        TEXT PRIMARY KEY,
    nome           TEXT,
    tipo           TEXT,                    -- jornal | portal | radio | investigativo | setorial
    escopo         TEXT NOT NULL DEFAULT 'to',  -- to: site do TO; nacional: só matéria que cita o TO
    origem         TEXT,                    -- curadoria | google_noticias | bio_instagram
    conta          TEXT,                    -- o @ do Instagram que leva ao site
    feed           TEXT,
    formato        TEXT,                    -- rss | wp_json; NULL: sem feed
    verificado_em  TEXT,
    lido_em        TEXT,
    itens_ultimo   INTEGER,
    erro           TEXT
);
"""

# Campos de perfil que um novo dado só sobrescreve quando não é nulo.
CAMPOS_PERFIL = (
    "ig_id", "nome", "bio", "seguidores", "seguindo", "n_posts", "privada", "verificada",
    "business", "categoria", "url_externa", "ultimo_post_em", "engajamento_medio",
    "posts_30d", "posts_recentes", "foto", "local_perfil",
)


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conectar(caminho: Path | str | None = None) -> sqlite3.Connection:
    caminho = Path(caminho or config.DB_PATH)
    if str(caminho) != ":memory:":
        caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    # Vários trabalhadores do navegador gravam ao mesmo tempo: espera a vez em vez de falhar, e o modo WAL deixa
    # ler enquanto outro grava.
    con.execute("PRAGMA busy_timeout = 30000")
    if str(caminho) != ":memory:":
        try:
            con.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:  # outro processo segura o banco agora; fica para a próxima conexão
            pass
    con.executescript(ESQUEMA)
    _migrar(con)
    return con


# Colunas criadas depois da primeira versão do banco: (tabela, coluna, tipo).
COLUNAS_NOVAS = [("relacionadas", "nome", "TEXT"), ("posts", "arquivo", "TEXT"), ("mencoes", "setor", "TEXT"),
                 ("visitas", "vazias", "INTEGER"),  # visitas seguidas sem post nem story novo (vigia)
                 # Endereços da imagem do post e da foto do perfil. Expiram em ~4 dias; capas.py guarda a imagem.
                 ("posts", "capa", "TEXT"), ("contas", "foto", "TEXT"),
                 # Leitura dos comentários (comentarios.py): quando, e quantos o post tinha, para reler se crescer.
                 ("mencoes", "comentarios_lidos_em", "TEXT"), ("mencoes", "comentarios_na_leitura", "INTEGER"),
                 # Busca de posts: a_investigar | do_to | descartado (busca_posts.py, investigacao.py).
                 ("posts_busca", "estado", "TEXT"),
                 # O local que a pessoa escreve no perfil (o X tem esse campo): pista para a investigação.
                 ("contas", "local_perfil", "TEXT"),
                 # Interações estimadas de um story pela mediana dos posts da conta (estimativa.py).
                 ("posts", "interacoes_estimadas", "INTEGER")]


def conectar_leitura(caminho: Path | str | None = None) -> sqlite3.Connection:
    """Conexão só de leitura, sem migração: o painel e as capas leem enquanto a vigilância grava."""
    caminho = Path(caminho or config.DB_PATH).resolve()
    con = sqlite3.connect(f"{caminho.as_uri()}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def _migrar(con: sqlite3.Connection) -> None:
    for tabela, coluna, tipo in COLUNAS_NOVAS:
        if coluna not in {l["name"] for l in con.execute(f"PRAGMA table_info({tabela})")}:
            con.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")


def upsert_conta(con: sqlite3.Connection, conta: dict, rede: str = "instagram") -> None:
    """Insere ou atualiza a conta sem apagar dados já conhecidos.

    Origem e data de descoberta ficam as da primeira vez; a profundidade fica a menor já vista.
    """
    colunas = ["rede", "username", *CAMPOS_PERFIL, "completo", "origem", "profundidade",
               "descoberta_em", "atualizada_em"]
    valores = {c: conta.get(c) for c in colunas}
    valores["rede"] = rede
    valores["completo"] = int(bool(conta.get("completo")))
    valores["profundidade"] = conta.get("profundidade", 0)
    valores["descoberta_em"] = valores["atualizada_em"] = agora()

    atualizacoes = [f"{c} = COALESCE(excluded.{c}, contas.{c})" for c in CAMPOS_PERFIL]
    atualizacoes += [
        "completo = MAX(contas.completo, excluded.completo)",
        "profundidade = MIN(contas.profundidade, excluded.profundidade)",
        "atualizada_em = excluded.atualizada_em",
    ]
    con.execute(
        f"INSERT INTO contas ({', '.join(colunas)}) VALUES ({', '.join('?' * len(colunas))}) "
        f"ON CONFLICT (rede, username) DO UPDATE SET {', '.join(atualizacoes)}",
        [valores[c] for c in colunas],
    )


def vincular_municipio(con, username: str, ibge_id: int, fonte: str, rede: str = "instagram") -> None:
    con.execute(
        "INSERT OR IGNORE INTO conta_municipio (rede, username, ibge_id, fonte) VALUES (?, ?, ?, ?)",
        (rede, username, ibge_id, fonte),
    )


def registrar_busca(con, fonte: str, termo: str, ibge_id: int | None, run_id: str | None,
                    n_resultados: int) -> None:
    con.execute(
        "INSERT INTO buscas (fonte, termo, ibge_id, executada_em, apify_run_id, n_resultados) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (fonte, termo) DO UPDATE SET "
        "executada_em = excluded.executada_em, apify_run_id = excluded.apify_run_id, "
        "n_resultados = excluded.n_resultados",
        (fonte, termo, ibge_id, agora(), run_id, n_resultados),
    )


def termos_executados(con, fonte: str) -> set[str]:
    return {r["termo"] for r in con.execute("SELECT termo FROM buscas WHERE fonte = ?", (fonte,))}
