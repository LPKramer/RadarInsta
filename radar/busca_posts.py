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
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from . import config, db, fofoca, mencoes, municipios as mun, noticias, perfis

ORIGEM = "busca_posts"
FONTE = "posts_nav"
REDE_RESERVA = "busca_posts"  # na tabela `reservas`, o termo fica no lugar do username
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


def da_energisa(username: str) -> bool:
    """Contas da própria Energisa e associadas (energisa, energisatocantins, grupoenergisa, reenergisa...)."""
    return "energisa" in (username or "").lower()


def contas_do_to(con) -> set[str]:
    """Contas ligadas ao TO pela classificação da IA ou pela investigação de quem posta."""
    return {l[0] for l in con.execute(
        "SELECT DISTINCT username FROM conta_municipio WHERE rede = 'instagram' AND fonte IN ('ia', 'investigacao')")}


def contas_de_fora(con, agora: datetime) -> set[str]:
    """Contas que a investigação situou fora do TO, com o veredito ainda válido."""
    from .investigacao import VALIDADE

    return {l[0] for l in con.execute(
        "SELECT username FROM investigacoes WHERE rede = 'instagram' AND do_to = 0 AND investigada_em >= ?",
        ((agora - VALIDADE).isoformat(timespec="seconds"),))}


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


def ingerir(con, termo: str, itens: list[dict], municipios, agora: datetime, dias: int = DIAS) -> Counter:
    resumo = Counter(vistos=len(itens))
    do_to, de_fora = contas_do_to(con), contas_de_fora(con, agora)
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
        db.upsert_conta(con, {"username": dono, "nome": nome, "origem": ORIGEM, "profundidade": 1})
        perfis.ingerir_posts(con, dono, [i for i, _ in posts])
        for item, estado in posts:
            post_id = str(item["id"])
            cursor = con.execute("INSERT OR IGNORE INTO posts_busca (rede, post_id, termo, achado_em, estado) "
                                 "VALUES ('instagram', ?, ?, ?, ?)", (post_id, termo, db.agora(), estado))
            if cursor.rowcount:
                resumo["novos"] += 1
            elif estado == "do_to":  # achado antes sem pista; agora com
                con.execute("UPDATE posts_busca SET estado = 'do_to' WHERE rede = 'instagram' AND post_id = ? "
                            "AND estado = 'a_investigar'", (post_id,))
            resumo[estado] += 1
            resumo["relevantes"] += 1
    con.commit()
    return resumo


def buscar(con, coletor, termo: str, agora: datetime | None = None, dias: int = DIAS) -> Counter:
    """Uma leitura: os posts do termo, filtrados e gravados."""
    agora = agora or datetime.now(timezone.utc)
    itens = coletor.buscar_posts(termo)
    resumo = ingerir(con, termo, itens, mun.carregar(con), agora, dias)
    con.execute("INSERT INTO buscas (fonte, termo, executada_em, n_resultados) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (fonte, termo) DO UPDATE SET executada_em = excluded.executada_em, "
                "n_resultados = excluded.n_resultados", (FONTE, termo, agora.isoformat(timespec="seconds"), len(itens)))
    con.commit()
    return resumo


def vencidos(con, agora: datetime, intervalo_h: float = config.VIGIA_BUSCA_POSTS_H) -> list[str]:
    """Termos cuja última busca passou de `intervalo_h` horas, do mais atrasado para o menos."""
    if intervalo_h <= 0:
        return []
    ultimas = dict(con.execute("SELECT termo, executada_em FROM buscas WHERE fonte = ?", (FONTE,)).fetchall())
    fila = []
    for posicao, termo in enumerate(TERMOS):
        ultima = perfis.ler_data(ultimas.get(termo))
        horas = float("inf") if ultima is None else (agora - ultima).total_seconds() / 3600
        if horas >= intervalo_h:
            fila.append((-horas, posicao, termo))
    return [termo for *_, termo in sorted(fila)]


def reservar(con, conta: str, agora: datetime, intervalo_h: float = config.VIGIA_BUSCA_POSTS_H) -> str | None:
    """O termo mais atrasado, já reservado para a conta (a transação imediata impede dois trabalhadores de pegar o
    mesmo)."""
    if con.in_transaction:
        con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        reservados = {l[0] for l in con.execute(
            "SELECT username FROM reservas WHERE rede = ? AND reservada_em > ?",
            (REDE_RESERVA, (agora - RESERVA_MAX).isoformat(timespec="seconds")))}
        termo = next((t for t in vencidos(con, agora, intervalo_h) if t not in reservados), None)
        if termo:
            con.execute("INSERT OR REPLACE INTO reservas (rede, username, conta, reservada_em) VALUES (?, ?, ?, ?)",
                        (REDE_RESERVA, termo, conta, agora.isoformat(timespec="seconds")))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return termo


def liberar(con, termo: str) -> None:
    if con.in_transaction:
        con.commit()
    con.execute("DELETE FROM reservas WHERE rede = ? AND username = ?", (REDE_RESERVA, termo))
    con.commit()


SQL_CANDIDATOS = """
SELECT p.*, k.tipo AS tipo_conta, c.seguidores, c.engajamento_medio,
       (SELECT GROUP_CONCAT(m.nome, ', ') FROM conta_municipio cm JOIN municipios m ON m.ibge_id = cm.ibge_id
         WHERE cm.rede = p.rede AND cm.username = p.username AND cm.fonte IN ('ia', 'investigacao'))
         AS municipios_conta,
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
    for arquivo in sorted(config.RAW_DIR.glob("*_busca_posts_*.json")):
        if arquivo.name[:8] < corte:
            continue
        conteudo = json.loads(arquivo.read_text(encoding="utf-8"))
        itens = [i for i in conteudo.get("itens") or [] if i.get("ownerUsername")]
        resumo.update(ingerir(con, (conteudo.get("entrada") or {}).get("termo") or "?", itens, municipios, agora))
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
        "SELECT sobre_energia FROM mencoes WHERE rede = 'instagram' AND post_id = ?", (p["post_id"],)).fetchone()
        for p in posts))


def situacao(con) -> dict:
    linha = con.execute(
        """SELECT COUNT(*), COUNT(DISTINCT p.username), SUM(x.sobre_energia),
                  SUM(x.sobre_energia = 1 AND x.sentimento = 'negativo')
           FROM posts_busca b JOIN posts p ON p.rede = b.rede AND p.post_id = b.post_id
           LEFT JOIN mencoes x ON x.rede = p.rede AND x.post_id = p.post_id""").fetchone()
    feitas = con.execute("SELECT COUNT(*), MAX(executada_em) FROM buscas WHERE fonte = ?", (FONTE,)).fetchone()
    estados = dict(con.execute("SELECT COALESCE(estado, 'do_to'), COUNT(*) FROM posts_busca GROUP BY 1").fetchall())
    return {"termos": len(TERMOS), "termos_ja_buscados": feitas[0], "ultima_busca": feitas[1],
            "posts_achados": linha[0], "contas": linha[1], "do_to": estados.get("do_to", 0),
            "a_investigar": estados.get("a_investigar", 0), "descartados": estados.get("descartado", 0),
            "sobre_energia": linha[2] or 0, "negativos": linha[3] or 0}
