"""Interações estimadas dos stories. O Instagram só mostra as curtidas e as respostas de um story ao dono da conta, então
o radar estima pelo que a mesma conta costuma ter no feed na mesma época:

- interpola: a mediana das interações (curtidas + comentários) dos posts de feed da conta mais próximos do story no
  tempo, antes ou depois dele. A mediana deixa de fora o post viral isolado;
- clipa: no máximo uma fração dos seguidores (TETO), para a conta pequena com um post fora da curva não inflar a soma;
- conta sem posts no banco: uma taxa padrão sobre os seguidores.

O número fica em `posts.interacoes_estimadas`, só nos stories, e entra nas interações do painel e na soma da mesma
notícia (fatos.py). Os posts que a conta publica depois mudam a vizinhança, então os stories recentes são recalculados.
"""
from datetime import datetime, timedelta, timezone
from statistics import median

VIZINHOS = 12  # posts de feed mais próximos no tempo
MIN_VIZINHOS = 3  # com menos, vale a taxa padrão sobre os seguidores
TETO = 0.05  # até 5% dos seguidores: 90% das contas da base ficam abaixo disso (mediana dos últimos posts, 11/11/2026)
TAXA_PADRAO = 0.007  # 0,7% dos seguidores: a mediana das contas da base, na mesma medição
DIAS = 30  # os stories dos últimos tantos dias são recalculados a cada vez

SQL_VIZINHOS = """
SELECT COALESCE(curtidas, 0) + COALESCE(comentarios, 0) FROM posts
WHERE rede = ? AND username = ? AND COALESCE(tipo, '') NOT IN ('Story', 'Materia') AND curtidas IS NOT NULL
  AND curtidas >= 0 AND publicado_em IS NOT NULL
ORDER BY ABS(julianday(publicado_em) - julianday(?)) LIMIT ?
"""
SQL_STORIES = """
SELECT p.rede, p.post_id, p.username, p.publicado_em, p.interacoes_estimadas, c.seguidores
FROM posts p
JOIN mencoes x ON x.rede = p.rede AND x.post_id = p.post_id AND x.sobre_energia = 1
LEFT JOIN contas c ON c.rede = p.rede AND c.username = p.username
WHERE p.rede = 'instagram' AND p.tipo = 'Story'
"""


def estimar(con, username: str, publicado_em: str, seguidores: int | None, rede: str = "instagram") -> int | None:
    """As interações estimadas de um story da conta publicado em `publicado_em`. None sem posts nem seguidores."""
    valores = [l[0] for l in con.execute(SQL_VIZINHOS, (rede, username, publicado_em, VIZINHOS))]
    if len(valores) >= MIN_VIZINHOS:
        base = float(median(valores))
    elif seguidores:
        base = seguidores * TAXA_PADRAO
    elif valores:
        base = float(median(valores))
    else:
        return None
    if seguidores:
        base = min(base, seguidores * TETO)
    return round(base)


def atualizar(con, dias: float | None = DIAS, agora: datetime | None = None) -> int:
    """Calcula (ou recalcula) a estimativa dos stories sobre energia publicados nos últimos `dias` (None: todos).
    Devolve quantos mudaram."""
    sql, args = SQL_STORIES, []
    if dias:
        agora = agora or datetime.now(timezone.utc)
        sql += " AND p.publicado_em >= ?"
        args.append((agora - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S"))
    mudaram = 0
    for l in con.execute(sql, args).fetchall():
        valor = estimar(con, l["username"], l["publicado_em"], l["seguidores"], l["rede"])
        if valor != l["interacoes_estimadas"]:
            con.execute("UPDATE posts SET interacoes_estimadas = ? WHERE rede = ? AND post_id = ?",
                        (valor, l["rede"], l["post_id"]))
            mudaram += 1
    con.commit()
    return mudaram
