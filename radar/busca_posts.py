"""Busca de posts por palavra-chave no Instagram, de qualquer conta, fora das contas monitoradas: as páginas e também
a população.

A página de busca por palavra-chave do site (instagram.com/explore/search/keyword/?q=...) mostra uns 24 posts por
termo, numa leitura. Os termos são a marca com as cidades maiores ("energisa palmas"), temas de energia ("falta de
energia araguaína") e o jeito de falar das pessoas ("acabou a energia", "energisa" sozinho).

Cada post cai num de três destinos (`triar`):
- fora: das contas da própria Energisa, antigo, sem palavra de energia, de outra distribuidora ou de outra área da
  Energisa, ou de conta que a investigação já situou fora do TO;
- do_to: o texto ou o local cita o Tocantins ou uma cidade do TO, ou a conta já é do TO (pela IA ou pela investigação);
- a_investigar: o resto. A DeepSeek determina a localização provável da conta (investigacao.py) e o post segue
  para a análise ou é descartado.

Os posts do TO entram na base de menções (tabela `posts`, com o termo e o estado em `posts_busca`); a IA analisa e a
menção negativa vira alerta. Na vigilância, cada termo roda a cada `config.VIGIA_BUSCA_POSTS_H` horas, reservado
entre as contas como as páginas.

O Threads e o X seguem o mesmo caminho, com os termos de cada rede (`TERMOS_POR_REDE`): os leitores de redes.py
devolvem os posts no formato da Apify, e o `post_id` ganha o prefixo da rede (th_, x_).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from . import config, db, fofoca, mencoes, municipios as mun, noticias, perfis

ORIGEM = "busca_posts"
REDES = ("instagram", "threads", "x")
FONTES = {"instagram": "posts_nav", "threads": "posts_threads", "x": "posts_x"}  # na tabela `buscas`
# Na tabela `reservas`, o termo fica no lugar do username; o mesmo nome rotula as respostas guardadas em data/raw/.
RESERVAS = {"instagram": "busca_posts", "threads": "busca_threads", "x": "busca_x"}
FONTE, REDE_RESERVA = FONTES["instagram"], RESERVAS["instagram"]
RESERVA_MAX = timedelta(minutes=30)
DIAS = 30

TERMOS = [
    # A marca, com as cidades maiores.
    "energisa tocantins", "energisa palmas", "energisa araguaína", "energisa gurupi", "energisa porto nacional",
    "energisa paraíso", "energisa colinas", "energisa guaraí",
    # Temas de energia.
    "falta de energia palmas", "falta de energia araguaína", "falta de energia gurupi", "falta de energia tocantins",
    "sem energia palmas", "queda de energia tocantins", "apagão tocantins", "conta de energia tocantins",
    # O jeito de falar das pessoas, sem cidade: quem decide se a conta é do TO é a investigação.
    "energisa", "acabou a energia", "sem energia de novo", "faltou energia", "energia caiu", "sem luz",
]
# O Threads não tem operadores de busca: termos curtos, como no Instagram.
TERMOS_THREADS = [
    "energisa", "energisa tocantins", "energisa palmas", "energisa araguaína", "energisa gurupi",
    "falta de energia palmas", "falta de energia araguaína", "sem energia palmas", "sem luz palmas",
    "acabou a energia", "conta de luz tocantins", "apagão tocantins",
]
# O X aceita OR, aspas e filtros: poucas consultas combinadas, sem os retuítes.
TERMOS_X = [
    'energisa (tocantins OR palmas OR araguaína OR gurupi OR "porto nacional") -filter:retweets',
    "energisa lang:pt -filter:retweets",
    '("falta de energia" OR "sem energia" OR "sem luz" OR "queda de energia") '
    "(palmas OR araguaína OR gurupi OR tocantins) -filter:retweets",
    '("acabou a energia" OR "faltou energia" OR "energia caiu") (palmas OR araguaína OR gurupi OR tocantins) '
    "-filter:retweets",
    '(apagão OR "conta de luz") tocantins -filter:retweets',
]
TERMOS_POR_REDE = {"instagram": TERMOS, "threads": TERMOS_THREADS, "x": TERMOS_X}


def intervalo(rede: str = "instagram") -> float:
    """Horas entre duas buscas do mesmo termo; 0 desliga a busca na rede."""
    return {"instagram": config.VIGIA_BUSCA_POSTS_H, "threads": config.VIGIA_BUSCA_THREADS_H,
            "x": config.VIGIA_BUSCA_X_H}[rede]


def da_energisa(username: str) -> bool:
    """Contas da própria Energisa e associadas (energisa, energisatocantins, grupoenergisa, reenergisa...)."""
    return "energisa" in (username or "").lower()


def contas_do_to(con, rede: str = "instagram") -> set[str]:
    """Contas ligadas ao TO pela classificação da IA ou pela investigação de quem posta."""
    return {l[0] for l in con.execute(
        "SELECT DISTINCT username FROM conta_municipio WHERE rede = ? AND fonte IN ('ia', 'investigacao')", (rede,))}


def contas_de_fora(con, agora: datetime, rede: str = "instagram") -> set[str]:
    """Contas que a investigação situou fora do TO, com o veredito ainda válido."""
    from .investigacao import VALIDADE

    return {l[0] for l in con.execute(
        "SELECT username FROM investigacoes WHERE rede = ? AND do_to = 0 AND investigada_em >= ?",
        (rede, (agora - VALIDADE).isoformat(timespec="seconds")))}


def cita_o_tocantins(texto: str, municipios) -> bool:
    """"Tocantins" ou uma cidade do TO (com a regra dos homônimos) no texto."""
    return "tocantins" in mun.normalizar(texto).split() or bool(fofoca.cidades_citadas("", texto, municipios))


def triar(item: dict, municipios, do_to: set[str], de_fora: set[str], limite: datetime) -> tuple[str, str | None]:
    """("fora", motivo), ("do_to", None) ou ("a_investigar", None)."""
    dono = item["ownerUsername"]
    if da_energisa(dono):
        return "fora", "energisa"
    data = perfis.ler_data(item.get("timestamp"))
    if data is None or data < limite:
        return "fora", "antigo"
    if not mencoes.menciona_energia(item.get("caption")):
        return "fora", "sem_energia"
    texto = f"{item.get('caption') or ''} {item.get('locationName') or ''}"
    if noticias.RE_OUTRA_DISTRIBUIDORA.search(mun.normalizar(texto)):
        return "fora", "outra_distribuidora"
    if dono in do_to or cita_o_tocantins(texto, municipios):
        return "do_to", None
    if dono in de_fora:
        return "fora", "conta_de_fora"
    return "a_investigar", None


def ingerir(con, termo: str, itens: list[dict], municipios, agora: datetime, dias: int = DIAS,
            rede: str = "instagram") -> Counter:
    resumo = Counter(vistos=len(itens))
    do_to, de_fora = contas_do_to(con, rede), contas_de_fora(con, agora, rede)
    limite = agora - timedelta(days=dias)
    por_dono = defaultdict(list)
    for item in itens:
        estado, motivo = triar(item, municipios, do_to, de_fora, limite)
        if estado == "fora":
            resumo[motivo] += 1
        else:
            por_dono[item["ownerUsername"]].append((item, estado))
    for dono, posts in por_dono.items():
        nome = next((i.get("ownerFullName") for i, _ in posts if i.get("ownerFullName")), None)
        # O X traz a bio e o local do perfil junto do post: pistas para a investigação, sem leitura nova.
        bio = next((i.get("ownerBiography") for i, _ in posts if i.get("ownerBiography")), None)
        local = next((i.get("ownerLocation") for i, _ in posts if i.get("ownerLocation")), None)
        db.upsert_conta(con, {"username": dono, "nome": nome, "bio": bio, "local_perfil": local, "origem": ORIGEM,
                              "profundidade": 1}, rede)
        perfis.ingerir_posts(con, dono, [i for i, _ in posts], rede)
        for item, estado in posts:
            post_id = str(item["id"])
            cursor = con.execute("INSERT OR IGNORE INTO posts_busca (rede, post_id, termo, achado_em, estado) "
                                 "VALUES (?, ?, ?, ?, ?)", (rede, post_id, termo, db.agora(), estado))
            if cursor.rowcount:
                resumo["novos"] += 1
            elif estado == "do_to":  # achado antes sem pista; agora com
                con.execute("UPDATE posts_busca SET estado = 'do_to' WHERE rede = ? AND post_id = ? "
                            "AND estado = 'a_investigar'", (rede, post_id))
            resumo[estado] += 1
            resumo["relevantes"] += 1
    con.commit()
    return resumo


def buscar(con, coletor, termo: str, agora: datetime | None = None, dias: int = DIAS,
           rede: str = "instagram") -> Counter:
    """Uma leitura: os posts do termo, filtrados e gravados."""
    agora = agora or datetime.now(timezone.utc)
    itens = coletor.buscar_posts(termo) if rede == "instagram" else coletor.buscar_rede(rede, termo)
    resumo = ingerir(con, termo, itens, mun.carregar(con), agora, dias, rede)
    con.execute("INSERT INTO buscas (fonte, termo, executada_em, n_resultados) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (fonte, termo) DO UPDATE SET executada_em = excluded.executada_em, "
                "n_resultados = excluded.n_resultados",
                (FONTES[rede], termo, agora.isoformat(timespec="seconds"), len(itens)))
    con.commit()
    return resumo


def vencidos(con, agora: datetime, intervalo_h: float | None = None, rede: str = "instagram") -> list[str]:
    """Termos cuja última busca passou de `intervalo_h` horas, do mais atrasado para o menos."""
    intervalo_h = intervalo(rede) if intervalo_h is None else intervalo_h
    if intervalo_h <= 0:
        return []
    ultimas = dict(con.execute("SELECT termo, executada_em FROM buscas WHERE fonte = ?", (FONTES[rede],)).fetchall())
    fila = []
    for posicao, termo in enumerate(TERMOS_POR_REDE[rede]):
        ultima = perfis.ler_data(ultimas.get(termo))
        horas = float("inf") if ultima is None else (agora - ultima).total_seconds() / 3600
        if horas >= intervalo_h:
            fila.append((-horas, posicao, termo))
    return [termo for *_, termo in sorted(fila)]


def reservar(con, conta: str, agora: datetime, intervalo_h: float | None = None,
             rede: str = "instagram") -> str | None:
    """O termo mais atrasado, já reservado para a conta (a transação imediata impede dois trabalhadores de pegar o
    mesmo)."""
    if con.in_transaction:
        con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        reservados = {l[0] for l in con.execute(
            "SELECT username FROM reservas WHERE rede = ? AND reservada_em > ?",
            (RESERVAS[rede], (agora - RESERVA_MAX).isoformat(timespec="seconds")))}
        termo = next((t for t in vencidos(con, agora, intervalo_h, rede) if t not in reservados), None)
        if termo:
            con.execute("INSERT OR REPLACE INTO reservas (rede, username, conta, reservada_em) VALUES (?, ?, ?, ?)",
                        (RESERVAS[rede], termo, conta, agora.isoformat(timespec="seconds")))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return termo


def liberar(con, termo: str, rede: str = "instagram") -> None:
    if con.in_transaction:
        con.commit()
    con.execute("DELETE FROM reservas WHERE rede = ? AND username = ?", (RESERVAS[rede], termo))
    con.commit()


SQL_CANDIDATOS = """
SELECT p.*, k.tipo AS tipo_conta, c.seguidores, c.engajamento_medio,
       (SELECT GROUP_CONCAT(m.nome, ', ') FROM conta_municipio cm JOIN municipios m ON m.ibge_id = cm.ibge_id
         WHERE cm.rede = p.rede AND cm.username = p.username AND cm.fonte IN ('ia', 'investigacao'))
         AS municipios_conta,
       (SELECT COALESCE(i.cidade || '-', '') || COALESCE(i.uf, '?') FROM investigacoes i
         WHERE i.rede = p.rede AND i.username = p.username) AS local_provavel,
       NULL AS triagem_energia, 1 AS da_busca
FROM posts_busca b
JOIN posts p ON p.rede = b.rede AND p.post_id = b.post_id
JOIN contas c ON c.rede = p.rede AND c.username = p.username
LEFT JOIN classificacoes k ON k.rede = p.rede AND k.username = p.username
WHERE COALESCE(b.estado, 'do_to') = 'do_to'
"""
SQL_SEM_ANALISE = " AND NOT EXISTS (SELECT 1 FROM mencoes x WHERE x.rede = p.rede AND x.post_id = p.post_id)"


def candidatos(con, reanalisar: bool = False) -> list[dict]:
    """Posts da busca já situados no TO e ainda sem análise da IA (com `reanalisar`, todos). A IA recebe a regra de
    que fato fora do TO não conta (`mencoes.FORMATO_BUSCA`)."""
    sql = SQL_CANDIDATOS + ("" if reanalisar else SQL_SEM_ANALISE) + " ORDER BY p.publicado_em DESC"
    return [dict(l) for l in con.execute(sql) if mencoes.menciona_energia(l["legenda"])]


def reprocessar_brutos(con, dias: int = DIAS, agora: datetime | None = None) -> Counter:
    """Passa de novo pela triagem os posts das buscas guardados em data/raw/ nos últimos `dias` (sem leitura nova):
    traz de volta os que a regra antiga descartou por não citar cidade."""
    import json

    agora = agora or datetime.now(timezone.utc)
    corte = (agora - timedelta(days=dias)).strftime("%Y%m%d")
    municipios = mun.carregar(con)
    resumo = Counter()
    for rede, rotulo in RESERVAS.items():
        for arquivo in sorted(config.RAW_DIR.glob(f"*_{rotulo}_*.json")):
            if arquivo.name[:8] < corte:
                continue
            conteudo = json.loads(arquivo.read_text(encoding="utf-8"))
            itens = [i for i in conteudo.get("itens") or [] if i.get("ownerUsername")]
            termo = (conteudo.get("entrada") or {}).get("termo") or "?"
            resumo.update(ingerir(con, termo, itens, municipios, agora, rede=rede))
            resumo["arquivos"] += 1
    return resumo


def analisar_e_alertar(con, cliente, aviso=print) -> int:
    """A IA analisa os achados pendentes; menção negativa vira alerta. Devolve quantos são sobre energia."""
    from .vigia import alertar

    posts = candidatos(con)
    if not posts:
        return 0
    mencoes.analisar(con, cliente, posts, aviso=lambda _: None)
    alertar(con, posts, aviso)
    return sum(bool(l and l[0]) for l in (con.execute(
        "SELECT sobre_energia FROM mencoes WHERE rede = ? AND post_id = ?", (p["rede"], p["post_id"])).fetchone()
        for p in posts))


def situacao(con, rede: str = "instagram") -> dict:
    linha = con.execute(
        """SELECT COUNT(*), COUNT(DISTINCT p.username), SUM(x.sobre_energia),
                  SUM(x.sobre_energia = 1 AND x.sentimento = 'negativo')
           FROM posts_busca b JOIN posts p ON p.rede = b.rede AND p.post_id = b.post_id
           LEFT JOIN mencoes x ON x.rede = p.rede AND x.post_id = p.post_id WHERE b.rede = ?""", (rede,)).fetchone()
    feitas = con.execute("SELECT COUNT(*), MAX(executada_em) FROM buscas WHERE fonte = ?", (FONTES[rede],)).fetchone()
    estados = dict(con.execute("SELECT COALESCE(estado, 'do_to'), COUNT(*) FROM posts_busca WHERE rede = ? GROUP BY 1",
                               (rede,)).fetchall())
    return {"termos": len(TERMOS_POR_REDE[rede]), "termos_ja_buscados": feitas[0], "ultima_busca": feitas[1],
            "posts_achados": linha[0], "contas": linha[1], "do_to": estados.get("do_to", 0),
            "a_investigar": estados.get("a_investigar", 0), "descartados": estados.get("descartado", 0),
            "sobre_energia": linha[2] or 0, "negativos": linha[3] or 0}
