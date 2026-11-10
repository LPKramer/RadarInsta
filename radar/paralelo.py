"""Coleta pelo navegador com várias contas do Instagram ao mesmo tempo, cada uma no seu próprio perfil do Edge.

Cada conta logada (`navegador-login --conta NOME`) vira um trabalhador num processo separado, com a sua janela, o seu
ritmo e os seus tetos de leituras. Eles dividem o serviço por uma fila no banco: cada um pega um lote de contas
monitoradas, coleta, marca como feito e pega o próximo. Se o Instagram interromper uma conta, ela devolve o lote e
para; as outras seguem. Rodar de novo no mesmo dia retoma o que ficou pendente.
"""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date

from . import config, db

TAREFAS = ("monitorar", "stories")
LOTE = 10


def rodada_de_hoje(tarefa: str) -> str:
    return f"{date.today().isoformat()}-{tarefa}"


def preparar(con, rodada: str, usernames: list[str]) -> int:
    """Põe as contas na fila da rodada (sem repetir as já feitas) e devolve à fila os lotes que um trabalhador
    interrompido deixou pela metade. Devolve quantas estão pendentes."""
    con.executemany("INSERT OR IGNORE INTO tarefas (rodada, username, estado) VALUES (?, ?, 'pendente')",
                    [(rodada, u) for u in usernames])
    con.execute("UPDATE tarefas SET estado = 'pendente', conta = NULL WHERE rodada = ? AND estado = 'em_andamento'",
                (rodada,))
    con.commit()
    return con.execute("SELECT COUNT(*) FROM tarefas WHERE rodada = ? AND estado = 'pendente'", (rodada,)).fetchone()[0]


def pegar_lote(con, rodada: str, conta: str, tamanho: int = LOTE) -> list[str]:
    """Reserva o próximo lote para a conta. A transação imediata impede dois trabalhadores de pegar o mesmo lote."""
    if con.in_transaction:
        con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        linhas = con.execute(
            """UPDATE tarefas SET estado = 'em_andamento', conta = ?, iniciada_em = ?
               WHERE rowid IN (SELECT rowid FROM tarefas WHERE rodada = ? AND estado = 'pendente' LIMIT ?)
               RETURNING username""", (conta, db.agora(), rodada, tamanho)).fetchall()
        con.commit()
    except Exception:
        con.rollback()
        raise
    return [l[0] for l in linhas]


def concluir(con, rodada: str, usernames: list[str]) -> None:
    con.executemany("UPDATE tarefas SET estado = 'feita', terminada_em = ? WHERE rodada = ? AND username = ?",
                    [(db.agora(), rodada, u) for u in usernames])
    con.commit()


def devolver(con, rodada: str, usernames: list[str]) -> None:
    con.executemany("UPDATE tarefas SET estado = 'pendente', conta = NULL WHERE rodada = ? AND username = ?",
                    [(rodada, u) for u in usernames])
    con.commit()


def _executar(con, coletor, tarefa: str, lote: list[str], aviso) -> Counter:
    # Importados aqui: ambos dependem de módulos que importam este, e o trabalhador só precisa deles ao coletar.
    from . import monitoramento, stories

    if tarefa == "monitorar":
        profundidade = dict(con.execute(
            f"SELECT username, profundidade FROM contas WHERE rede = 'instagram' "
            f"AND username IN ({', '.join('?' * len(lote))})", lote).fetchall())
        contas = [{"username": u, "profundidade": profundidade.get(u, 0)} for u in lote]
        return Counter(monitoramento.coletar_novos(con, coletor, contas, aviso=lambda _: None))
    resumo = Counter()
    for username in lote:
        resumo.update(stories.coletar(con, coletor, username))
    return resumo


def processar(con, coletor, rodada: str, tarefa: str, conta: str, aviso=print) -> Counter:
    """O laço de um trabalhador: pega lote, coleta, conclui, até a fila acabar ou o Instagram interromper a conta."""
    from .navegador import ColetaInterrompida

    resumo = Counter()
    while lote := pegar_lote(con, rodada, conta):
        try:
            resumo.update(_executar(con, coletor, tarefa, lote, aviso))
        except ColetaInterrompida:
            devolver(con, rodada, lote)  # outra conta (ou a próxima rodada) faz este lote
            raise
        concluir(con, rodada, lote)
        if tarefa == "stories":  # o monitoramento já conta as contas no próprio resumo
            resumo["contas"] += len(lote)
        aviso(f"[{conta}] {tarefa}: +{len(lote)} contas ({dict(resumo)})")
    return resumo


def trabalhador(conta: str, tarefa: str, rodada: str, caminho_banco: str, visivel: bool) -> dict:
    """Roda num processo próprio: abre o Edge da conta e processa a fila. Devolve um resumo, mesmo se parar."""
    from . import navegador

    con = db.conectar(caminho_banco)
    resumo = Counter()
    try:
        with navegador.abrir(visivel, conta=conta) as coletor:
            resumo = processar(con, coletor, rodada, tarefa, conta)
            return {"conta": conta, **resumo}
    except (navegador.ColetaInterrompida, navegador.SemLogin) as erro:
        return {"conta": conta, **resumo, "parou": str(erro)}
    except Exception as erro:  # perfil já aberto em outra janela, Edge ausente...
        return {"conta": conta, **resumo, "parou": f"{type(erro).__name__}: {erro}"[:300]}


def executar(tarefa: str, contas: list[str], usernames: list[str], aviso=print,
             visivel: bool = config.NAV_VISIVEL) -> list[dict]:
    """Prepara a fila e solta um trabalhador por conta do Instagram, em paralelo."""
    if tarefa not in TAREFAS:
        raise ValueError(f"tarefa desconhecida: {tarefa}")
    rodada = rodada_de_hoje(tarefa)
    con = db.conectar()
    pendentes = preparar(con, rodada, usernames)
    con.close()
    aviso(f"Rodada {rodada}: {pendentes} contas na fila para {len(contas)} conta(s) do Instagram: {', '.join(contas)}.")
    if not pendentes:
        return []
    resultados = []
    with ProcessPoolExecutor(max_workers=len(contas)) as executor:
        futuros = [executor.submit(trabalhador, conta, tarefa, rodada, str(config.DB_PATH), visivel)
                   for conta in contas]
        for futuro in as_completed(futuros):
            resultado = futuro.result()
            resultados.append(resultado)
            aviso(f"[{resultado['conta']}] terminou: {resultado}")
    return resultados


def situacao(con, rodada: str) -> dict:
    return dict(con.execute("SELECT estado, COUNT(*) FROM tarefas WHERE rodada = ? GROUP BY estado",
                            (rodada,)).fetchall())
