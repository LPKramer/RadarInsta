"""Vigilância contínua: em vez de uma rodada por dia, o radar fica rodando e visita primeiro as páginas mais atrasadas.

Cada conta monitorada tem um intervalo entre visitas, conforme a prioridade:
- foco (1 h): influenciadores e páginas de fofoca;
- alta (2 h): portais, páginas de cidade, plantão e jornalistas que já falaram de energia ou têm muitos seguidores;
- média (6 h): os demais desses tipos;
- baixa (24 h): órgãos públicos, políticos e o resto.
Os trabalhadores (um por conta do Instagram logada, em paralelo) pegam sempre a página mais atrasada em relação ao
seu intervalo, reservando-a no banco para outro trabalhador não pegar a mesma. Cada visita traz os posts novos e, se
houver, os stories. Quando um post passa no filtro de energia, a IA o classifica na hora e as menções negativas vão
para data/alertas.txt.

Segurança das contas: os trabalhadores entram com alguns minutos de diferença; uma conta que o Instagram barra para
sozinha e as outras assumem as páginas dela; se duas forem barradas em menos de uma hora, o problema provavelmente é
a conexão, e todas param (parada geral). Contas logadas com a vigilância já rodando entram sozinhas.

Volta (`vigiar --volta`): em vez de ficar rodando, passa uma vez por todas as páginas, por prioridade, e termina. O
agendar.ps1 dispara duas voltas por dia; os stories duram 24 h, então duas voltas não perdem nenhum.
"""
import random
import sqlite3
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from functools import partial

from . import config, db, perfis

TIPOS_FOCO = ["influenciador_local", "perfil_fofoca"]
TIPOS_NOTICIA = ["portal_noticias", "pagina_cidade_comunidade", "policial_plantao", "jornalista_radialista"]
RESERVA_MAX = timedelta(minutes=30)  # reserva mais velha que isso é de um trabalhador que caiu: vale de novo
ESPERA_MAXIMA = 300  # sem página vencida, o trabalhador dorme no máximo isso antes de olhar de novo
RECALCULAR = timedelta(minutes=30)  # prioridades mudam quando aparece menção nova
CONSOLIDAR = timedelta(minutes=10)  # de quanto em quanto tempo agrupa as menções novas da mesma notícia (fatos.py)
REVISAR_DIAS = 3  # a revisão dos agrupamentos separados olha os pares com um agrupamento desses últimos dias
OLHAR_SITES = timedelta(minutes=10)  # de quanto em quanto tempo vê se a rodada dos sites (a cada hora) venceu
ARQUIVO_ALERTAS = config.DATA_DIR / "alertas.txt"
ARQUIVO_PARAR = config.NAVEGADOR_DIR / "PARAR"  # existindo, todos os trabalhadores param
ESCALONAR = 180  # segundos entre a entrada de um trabalhador e a do seguinte
BLOQUEIOS_PARA_PARAR = 2  # contas barradas pelo Instagram, dentro de JANELA_PARADA, que param todas
JANELA_PARADA = 3600
PROCURAR_CONTAS = 60  # de quanto em quanto tempo procura contas logadas depois do início
MAX_CONTAS = 12  # cada Edge usa ~0,5 GB de memória
RECUO_MAXIMO = 3  # visitas vazias seguidas dobram o intervalo até 2³ = 8 vezes (sempre dentro do prazo dos stories)
# Leituras por página numa volta com 12 h desde a anterior, estimadas em nov/2026: a visita, a página de stories em
# 30% a 55% delas (a volta da noite pega o dia) e ~10% de comentários, buscas e investigação. Depois de dias sem
# vigia é mais (2,4 em 13/nov, recolhendo 2 dias).
LEITURAS_POR_PAGINA_NA_VOLTA = 1.6
TENTATIVAS = 3  # falha inesperada (o Edge não abriu, travou...): a conta tenta de novo até isso
REINICIAR = 300  # segundos até a nova tentativa


class Parada(BaseException):
    """Pediram para parar (ARQUIVO_PARAR). BaseException, como o Ctrl+C, para nenhum `except Exception` engolir."""


def cochilar(segundos: float, parar=lambda: False, dormir=time.sleep) -> None:
    """Dorme em pedaços de até 1 minuto, acordando se pedirem para parar."""
    restante = segundos
    while restante > 0:
        if parar():
            raise Parada
        pedaco = min(restante, 60)
        dormir(pedaco)
        restante -= pedaco
    if parar():
        raise Parada


def intervalos(con) -> dict[str, float]:
    """Horas entre visitas de cada conta monitorada."""
    from . import monitoramento

    info = {l["username"]: dict(l) for l in con.execute(
        "SELECT c.username, c.seguidores, k.tipo FROM contas c "
        "LEFT JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username WHERE c.rede = 'instagram'")}
    com_mencao = {l[0] for l in con.execute(
        "SELECT DISTINCT p.username FROM mencoes x JOIN posts p ON p.rede = x.rede AND p.post_id = x.post_id "
        "WHERE x.sobre_energia = 1")}
    influenciadores = {l[0] for l in con.execute(
        "SELECT username FROM listas WHERE rede = 'instagram' AND lista = 'influenciadores_to'")}
    # As rádios aprovadas (radios.py): no mínimo a cada 6 h, para os vídeos chegarem à transcrição a tempo.
    radios = {l[0] for l in con.execute("SELECT username FROM listas WHERE rede = 'instagram' AND lista = 'radios_to'")}
    resultado = {}
    for conta in monitoramento.monitoradas(con):
        u = conta["username"]
        dados = info.get(u, {})
        if dados.get("tipo") in TIPOS_FOCO or u in influenciadores:
            resultado[u] = config.VIGIA_HORAS["foco"]
        elif dados.get("tipo") in TIPOS_NOTICIA:
            alta = u in com_mencao or (dados.get("seguidores") or 0) >= config.VIGIA_SEGUIDORES_ALTA
            resultado[u] = config.VIGIA_HORAS["alta" if alta else "media"]
        else:
            resultado[u] = config.VIGIA_HORAS["baixa"]
        if u in radios:
            resultado[u] = min(resultado[u], config.VIGIA_HORAS["media"])
    return resultado


def _reservadas(con, agora: datetime) -> set[str]:
    return {l[0] for l in con.execute("SELECT username FROM reservas WHERE rede = 'instagram' AND reservada_em > ?",
                                      ((agora - RESERVA_MAX).isoformat(timespec="seconds"),))}


def prazo(intervalo: float, vazias: int = 0) -> float:
    """Horas até a página vencer: o intervalo dela, dobrado a cada visita seguida que não trouxe nada novo (até
    RECUO_MAXIMO vezes), mas nunca mais que o prazo dos stories. Uma visita com novidade volta ao intervalo normal."""
    return min(intervalo * 2 ** min(vazias or 0, RECUO_MAXIMO), config.VIGIA_PRAZO_STORIES_H)


def _visitas(con) -> dict[str, tuple[str, int]]:
    return {l[0]: (l[1], l[2] or 0) for l in con.execute(
        "SELECT username, ultima_em, vazias FROM visitas WHERE rede = 'instagram'")}


def atrasadas(con, agora: datetime, horas: dict[str, float], volta: bool = False,
              inicio: datetime | None = None) -> list[tuple[float, str]]:
    """Contas vencidas e livres, na ordem de visita, com o atraso (tempo desde a última visita ÷ prazo).

    Primeiro as que passaram do prazo dos stories (ou nunca foram visitadas), da mais velha para a mais nova e, no
    empate, as de intervalo menor: sem isso, com pouca capacidade, os stories das páginas de 24 h se perdem. Depois,
    com a capacidade que sobra, as revisitas pela ordem de atraso.

    Na volta (`volta=True`), vencida é toda página sem visita nas VIGIA_VOLTA_H horas antes de `inicio` (o começo da
    volta; sem ele, `agora`), qualquer que seja o intervalo dela. Contando do começo, o que a volta já visitou não
    entra de novo, mesmo que ela passe de VIGIA_VOLTA_H horas. Depois das que têm stories em risco, vão primeiro as de
    prioridade maior (intervalo menor)."""
    visitas = _visitas(con)
    reservadas = _reservadas(con, agora)
    fila = []
    for username, intervalo in horas.items():
        if username in reservadas:
            continue
        ultima_em, vazias = visitas.get(username, (None, 0))
        ultima = perfis.ler_data(ultima_em)
        desde = float("inf") if ultima is None else (agora - ultima).total_seconds() / 3600
        if volta:
            sem_visita = desde if ultima is None else ((inicio or agora) - ultima).total_seconds() / 3600
            if sem_visita >= config.VIGIA_VOLTA_H:
                em_risco = desde >= config.VIGIA_PRAZO_STORIES_H
                fila.append(((0, -desde, intervalo) if em_risco else (1, intervalo, -desde), desde / intervalo,
                             username))
            continue
        atraso = desde / prazo(intervalo, vazias)
        if atraso >= 1:
            stories_em_risco = desde >= config.VIGIA_PRAZO_STORIES_H
            ordem = (0, -desde, intervalo) if stories_em_risco else (1, -atraso, intervalo)
            fila.append((ordem, atraso, username))
    return [(atraso, username) for ordem, atraso, username in sorted(fila, key=lambda x: (x[0], x[2]))]


def reservar(con, conta: str, agora: datetime, horas: dict[str, float], volta: bool = False,
             inicio: datetime | None = None) -> str | None:
    """A página mais atrasada, já reservada para a conta. A transação imediata impede dois trabalhadores de pegar a
    mesma."""
    if con.in_transaction:
        con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        fila = atrasadas(con, agora, horas, volta, inicio)
        username = fila[0][1] if fila else None
        if username:
            con.execute("INSERT OR REPLACE INTO reservas (rede, username, conta, reservada_em) "
                        "VALUES ('instagram', ?, ?, ?)", (username, conta, agora.isoformat(timespec="seconds")))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return username


def _novidades(con, username: str) -> tuple:
    """O post mais novo da grade e quantos stories a conta tem gravados: se mudou, a visita trouxe novidade."""
    return tuple(con.execute(
        "SELECT MAX(CASE WHEN COALESCE(tipo, '') != 'Story' THEN publicado_em END), SUM(tipo = 'Story') "
        "FROM posts WHERE rede = 'instagram' AND username = ?", (username,)).fetchone())


def marcar_visita(con, username: str, novidade: bool) -> None:
    """Conta as visitas seguidas sem novidade (o intervalo da página cresce com elas; ver `prazo`)."""
    con.execute("UPDATE visitas SET vazias = CASE WHEN ? THEN 0 ELSE COALESCE(vazias, 0) + 1 END "
                "WHERE rede = 'instagram' AND username = ?", (int(novidade), username))
    con.commit()


def liberar(con, username: str) -> None:
    if con.in_transaction:
        con.commit()
    con.execute("DELETE FROM reservas WHERE rede = 'instagram' AND username = ?", (username,))
    con.commit()


def segundos_ate_a_proxima(con, agora: datetime, horas: dict[str, float]) -> float:
    """Quanto falta para a próxima página vencer (para o trabalhador dormir sem gastar leituras)."""
    visitas = _visitas(con)
    faltas = []
    for username, intervalo in horas.items():
        ultima_em, vazias = visitas.get(username, (None, 0))
        ultima = perfis.ler_data(ultima_em)
        if ultima is not None:
            faltas.append((ultima + timedelta(hours=prazo(intervalo, vazias)) - agora).total_seconds())
    return min(max(min(faltas, default=ESPERA_MAXIMA), 30), ESPERA_MAXIMA)


def alertar(con, posts: list[dict], aviso=print) -> int:
    """Menção negativa sobre energia vira uma linha em data/alertas.txt (posts do Instagram, stories e matérias)."""
    n = 0
    for post in posts:
        linha = con.execute("SELECT sobre_energia, sentimento, tema, setor, resumo FROM mencoes WHERE rede = ? "
                            "AND post_id = ?", (post["rede"], post["post_id"])).fetchone()
        if not linha or not linha["sobre_energia"] or linha["sentimento"] != "negativo":
            continue
        redes = {"web": "site ", "threads": "Threads @", "x": "X @", "radio": "rádio ao vivo "}
        origem = f"{redes.get(post['rede'], '@')}{post['username']}"
        alerta = (f"{db.agora()}  {origem}  setor: {linha['setor']}  {linha['tema']}  {linha['resumo']}  "
                  f"{post.get('url') or ''}")
        ARQUIVO_ALERTAS.parent.mkdir(parents=True, exist_ok=True)
        with ARQUIVO_ALERTAS.open("a", encoding="utf-8") as arquivo:
            arquivo.write(alerta + "\n")
        aviso(f"ALERTA {alerta}")
        n += 1
    return n


def _triador(cliente):
    """A triagem dos stories de cada página visitada (triagem.py), se a IA está disponível."""
    if cliente is None:
        return None
    from . import triagem

    return lambda textos: triagem.triar_textos(cliente, textos)


def classificar_e_alertar(con, cliente, username: str, aviso=print) -> int:
    """Classifica na hora os posts novos da conta que passaram no filtro; menção negativa vira alerta."""
    from . import mencoes

    candidatos = mencoes.candidatos(con, username=username)
    if not candidatos:
        return 0
    mencoes.analisar(con, cliente, candidatos, aviso=lambda _: None)
    novas = 0
    for post in candidatos:
        linha = con.execute("SELECT sobre_energia, sentimento, tema, setor, resumo FROM mencoes "
                            "WHERE rede = 'instagram' AND post_id = ?", (post["post_id"],)).fetchone()
        if not linha or not linha["sobre_energia"]:
            continue
        novas += 1
        if linha["sentimento"] == "negativo":
            alerta = (f"{db.agora()}  @{username}  setor: {linha['setor']}  {linha['tema']}  {linha['resumo']}  "
                      f"{post.get('url') or ''}")
            ARQUIVO_ALERTAS.parent.mkdir(parents=True, exist_ok=True)
            with ARQUIVO_ALERTAS.open("a", encoding="utf-8") as arquivo:
                arquivo.write(alerta + "\n")
            aviso(f"ALERTA {alerta}")
    return novas


def vigiar(con, coletor, conta: str, ate: datetime, aviso=print, ia=None, relogio=None, dormir=time.sleep,
           parar=lambda: False, volta: bool = False) -> Counter:
    """O laço de um trabalhador: reserva a página mais atrasada, coleta, libera, até a hora `ate` ou até pedirem
    para parar. No teto diário de leituras da conta, espera em vez de parar. Entre uma página e outra, faz a busca
    de posts por palavra-chave que estiver vencida (busca_posts.py). Na volta (`volta=True`), termina quando não
    sobra página sem visita recente (ver `atrasadas`)."""
    from . import busca_posts, comentarios, investigacao, mencoes, monitoramento
    from .navegador import ColetaInterrompida, TetoAtingido

    relogio = relogio or (lambda: datetime.now(timezone.utc))
    resumo = Counter()
    horas, calculado = intervalos(con), relogio()
    inicio = calculado  # a volta conta a visita recente daqui, não da hora de cada reserva
    consolidado_em = sites_em = estimado_em = None

    def esperar(segundos):
        cochilar(min(segundos, max((ate - relogio()).total_seconds(), 1)), parar, dormir)

    try:
        while (agora := relogio()) < ate:
            if parar():
                break
            if agora - calculado > RECALCULAR:
                horas, calculado = intervalos(con), agora
            if ia is not None and (consolidado_em is None or agora - consolidado_em > CONSOLIDAR):
                consolidado_em = agora  # a mesma notícia (fatos.py): sem leitura, então vem antes do teto
                resumo["fatos"] += consolidar(con, ia, conta, agora, aviso)
            if estimado_em is None or agora - estimado_em > CONSOLIDAR:
                estimado_em = agora  # as interações estimadas dos stories (estimativa.py): só o banco
                resumo["stories_estimados"] += estimar_stories(con, aviso)
            if sites_em is None or agora - sites_em > OLHAR_SITES:
                sites_em = agora  # os sites de notícia (sites.py): sem leitura do Instagram, então antes do teto
                resumo["materias"] += ler_sites(con, ia, agora, aviso)
            teto = coletor.espera_do_teto() if hasattr(coletor, "espera_do_teto") else 0
            if teto:
                if teto > 600:
                    aviso(f"teto diário de {config.NAV_MAX_DIA} leituras desta conta; volta em {teto / 60:.0f} min")
                esperar(teto + random.uniform(5, 60))
                continue
            termo = busca_posts.reservar(con, conta, agora) if hasattr(coletor, "buscar_posts") else None
            if termo:  # busca de posts por palavra-chave vencida: tem a vez antes das páginas
                try:
                    resumo["posts_buscados"] += busca_posts.buscar(con, coletor, termo, agora)["novos"]
                except TetoAtingido:
                    continue
                finally:
                    busca_posts.liberar(con, termo)
                if ia is not None:
                    try:
                        resumo["mencoes"] += busca_posts.analisar_e_alertar(con, ia, aviso)
                    except Exception as erro:
                        aviso(f"IA indisponível ({type(erro).__name__}); classifique depois com: "
                              "python -m radar buscar-posts --so-ia")
                        ia = None
                continue
            post = comentarios.reservar(con, conta, agora) if hasattr(coletor, "comentarios") else None
            if post:  # comentários de uma menção vencida: a voz da população sobre o post
                try:
                    resumo["comentarios"] += monitoramento.coletar_comentarios(
                        con, coletor, [post], config.COMENTARIOS_POR_POST, aviso=lambda _: None)
                    mencoes.marcar_comentarios_lidos(con, [post])
                except TetoAtingido:
                    continue
                finally:
                    comentarios.liberar(con, post["post_id"])
                if ia is not None:
                    try:
                        resumo["relatos"] += comentarios.analisar_e_alertar(con, ia, [post["post_id"]], aviso)
                    except Exception as erro:
                        aviso(f"IA indisponível ({type(erro).__name__}); analise depois com: "
                              "python -m radar reacoes --posts 0")
                        ia = None
                continue
            autores = investigacao.reservar(con, conta, agora) if ia is not None else []
            if autores:  # quem postou sem dizer de onde é: a DeepSeek situa a conta (às vezes lendo o perfil)
                try:
                    resumo["investigados"] += investigacao.investigar(con, ia, coletor, autores,
                                                                      aviso=lambda _: None)["investigados"]
                    resumo["mencoes"] += busca_posts.analisar_e_alertar(con, ia, aviso)
                except TetoAtingido:
                    continue
                except ColetaInterrompida:
                    raise
                except Exception as erro:
                    aviso(f"IA indisponível ({type(erro).__name__}); investigue depois com: "
                          "python -m radar buscar-posts --so-ia")
                    ia = None
                finally:
                    investigacao.liberar(con, autores)
                continue
            username = reservar(con, conta, agora, horas, volta, inicio)
            if not username:
                if volta:  # as páginas que faltam estão com outros trabalhadores, ou não falta nenhuma
                    aviso(f"sem página para visitar nesta volta: {dict(resumo)}")
                    break
                esperar(segundos_ate_a_proxima(con, agora, horas))
                continue
            profundidade = con.execute("SELECT profundidade FROM contas WHERE rede = 'instagram' AND username = ?",
                                       (username,)).fetchone()
            antes = _novidades(con, username)
            try:
                resumo.update(monitoramento.coletar_novos(
                    con, coletor, [{"username": username, "profundidade": profundidade[0] if profundidade else 0}],
                    aviso=lambda _: None, agora=agora, triar=_triador(ia)))
            except TetoAtingido:
                continue  # a próxima volta espera o teto
            finally:
                liberar(con, username)
            marcar_visita(con, username, novidade=_novidades(con, username) != antes)
            if ia is not None:
                try:
                    resumo["mencoes"] += classificar_e_alertar(con, ia, username, aviso)
                except Exception as erro:  # sem rede ou sem saldo: a classificação fica para o `mencoes`
                    aviso(f"IA indisponível ({type(erro).__name__}); classifique depois com: python -m radar mencoes")
                    ia = None
            if resumo["contas"] % 25 == 0:
                aviso(f"{dict(resumo)}")
    except Parada:
        aviso("parando a pedido")
    return resumo


def consolidar(con, ia, conta: str, agora: datetime, aviso=print) -> int:
    """Agrupa as menções novas que tratam da mesma notícia, uma consolidação por vez entre os trabalhadores. Uma falha
    aqui não desliga a IA dos outros passos: fica para a próxima vez."""
    from . import fatos

    if not fatos.reservar_vez(con, conta, agora):
        return 0
    try:
        ligadas = fatos.consolidar(con, ia, aviso=lambda _: None)["ligadas"]
        fatos.revisar(con, ia, dias=REVISAR_DIAS, aviso=aviso)  # os agrupamentos recentes que ficaram separados
        return ligadas
    except Exception as erro:
        aviso(f"mesma notícia ficou para depois ({type(erro).__name__})")
        return 0
    finally:
        fatos.liberar_vez(con)


def estimar_stories(con, aviso=print) -> int:
    """Recalcula as interações estimadas dos stories recentes. Uma falha (o banco ocupado) fica para a próxima vez."""
    from . import estimativa

    try:
        return estimativa.atualizar(con)
    except sqlite3.OperationalError as erro:
        con.rollback()
        aviso(f"estimativa dos stories ficou para depois ({erro})")
        return 0


def ler_sites(con, ia, agora: datetime, aviso=print) -> int:
    """A rodada dos sites de notícia, quando venceu (uma por hora entre todos os trabalhadores). Uma falha aqui não
    desliga a IA dos outros passos: fica para a próxima rodada."""
    from . import sites

    try:
        return sites.rodada(con, ia, agora, aviso)["novas"]
    except Exception as erro:
        aviso(f"sites de notícia ficaram para depois ({type(erro).__name__})")
        return 0


def ia_disponivel() -> bool:
    """A DeepSeek responde daqui? (Sem isso, uma chamada pode travar o trabalhador por minutos.)"""
    from . import classificacao_deepseek as ds

    if config.PROVEDOR_IA != "deepseek" or not config.DEEPSEEK_API_KEY:
        return False
    try:
        ds.testar(ds.criar_cliente().with_options(timeout=10, max_retries=0))
    except Exception:
        return False
    return True


def pedir_parada(motivo: str) -> None:
    ARQUIVO_PARAR.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_PARAR.write_text(f"{db.agora()}  {motivo}\n", encoding="utf-8")


def parada_pedida() -> bool:
    return ARQUIVO_PARAR.exists()


def trabalhador(conta: str, ate: str, caminho_banco: str, visivel: bool, com_ia: bool, atraso: float = 0,
                volta: bool = False) -> dict:
    """Roda num processo próprio: espera a sua vez, abre o Edge da conta e vigia até `ate` (ISO, UTC) ou, na volta,
    até não sobrar página."""
    from . import navegador

    def aviso(mensagem):
        print(f"[{conta}] {mensagem}", flush=True)

    def parar():  # parada geral ou só desta conta (navegador-desativar)
        return parada_pedida() or navegador.desativada(conta) is not None

    def dormir_ritmo(segundos):
        cochilar(segundos, parar)

    fim = datetime.fromisoformat(ate)
    try:
        if atraso:
            cochilar(atraso, parar)
        con = db.conectar(caminho_banco)
        ia = None
        if com_ia and ia_disponivel():
            from . import classificacao_deepseek as ds

            ia = ds.criar_cliente()
        aviso("vigiando")
        # A volta lê num ritmo fixo; a vigilância contínua espalha o teto do dia (ver `Ritmo.intervalo_uniforme`).
        with navegador.abrir(visivel, conta=conta, dormir=dormir_ritmo, uniforme=True,
                             por_hora=config.VIGIA_VOLTA_POR_HORA if volta else None) as coletor:
            return {"conta": conta, **vigiar(con, coletor, conta, fim, aviso, ia, parar=parar, volta=volta)}
    except Parada:
        motivo = navegador.desativada(conta)
        return {"conta": conta, "parou": f"desativada: {motivo}", "sem_login": True} if motivo else {
            "conta": conta, "parou": "a pedido"}
    except navegador.ColetaInterrompida as erro:
        return {"conta": conta, "parou": str(erro), "bloqueio": True}
    except navegador.SemLogin as erro:
        return {"conta": conta, "parou": str(erro), "sem_login": True}
    except Exception as erro:
        return {"conta": conta, "parou": f"{type(erro).__name__}: {erro}"[:300]}


def _falha_passageira(resultado: dict) -> bool:
    """Parou por erro inesperado, e não por aviso do Instagram, falta de login ou pedido de parada."""
    return ("parou" in resultado and not resultado.get("bloqueio") and not resultado.get("sem_login")
            and resultado["parou"] != "a pedido")


def contas_novas(conhecidas: set[str]) -> list[str]:
    """Contas logadas no navegador que ainda não estão na vigilância, menos as desativadas. Uma conta com o Edge aberto
    (no meio do login) só entra depois que a janela fecha."""
    from . import navegador

    return [c for c in navegador.contas_configuradas()
            if c not in conhecidas and not navegador.desativada(c) and "instagram" in navegador.redes_da_conta(c)
            and navegador.tem_sessao(navegador.pasta_conta(c)) is True]


def executar(contas: list[str], horas: float, com_ia: bool = True, aviso=print, visivel: bool = config.NAV_VISIVEL,
             procurar_novas=None, trabalhar=trabalhador, executor_cls=ProcessPoolExecutor,
             esperar_contas=time.sleep, volta: bool = False) -> list[dict]:
    """Um trabalhador por conta, entrando com ESCALONAR segundos de diferença. `procurar_novas(conhecidas)` devolve
    contas logadas depois do início, que entram também. Duas contas barradas em menos de uma hora param todas.

    Cada conta roda no seu próprio processo (um executor por conta): se um processo morre, os das outras contas
    seguem, e a nova tentativa abre um processo novo.

    Na volta (`volta=True`), cada trabalhador para quando não sobra página, e `horas` é só o limite. A volta usa as
    contas logadas no início; uma conta logada depois entra na volta seguinte."""
    if volta:
        trabalhar, procurar_novas = partial(trabalhar, volta=True), None
    ARQUIVO_PARAR.unlink(missing_ok=True)
    fim = datetime.now(timezone.utc) + timedelta(hours=horas)
    resultados, bloqueios, conhecidas, tentativas = [], [], set(), Counter()
    pendentes, executores = {}, {}

    def iniciar(conta, atraso):
        conhecidas.add(conta)
        if len(pendentes) >= MAX_CONTAS:
            aviso(f"[{conta}] fica de fora: no máximo {MAX_CONTAS} contas ao mesmo tempo")
            return
        executor = executor_cls(max_workers=1)
        futuro = executor.submit(trabalhar, conta, fim.isoformat(), str(config.DB_PATH), visivel, com_ia, atraso)
        pendentes[futuro], executores[futuro] = conta, executor
        aviso(f"[{conta}] entra na vigilância" + (f" em {atraso / 60:.0f} min" if atraso >= 60 else ""))

    try:

        for i, conta in enumerate(contas):
            iniciar(conta, i * ESCALONAR + (random.uniform(0, 60) if i else 0))
        avisou_vazio = False
        while pendentes or (procurar_novas and not parada_pedida() and datetime.now(timezone.utc) < fim):
            feitos = set()
            if pendentes:
                feitos, _ = wait(set(pendentes), timeout=PROCURAR_CONTAS, return_when=FIRST_COMPLETED)
                avisou_vazio = False
            else:  # nenhuma conta rodando: espera alguém logar uma
                if not avisou_vazio:
                    aviso("Nenhuma conta vigiando agora. Logue outra (python -m radar navegador-login --conta conta2) "
                          "e ela entra sozinha, ou encerre com: python -m radar vigiar --parar")
                    avisou_vazio = True
                esperar_contas(PROCURAR_CONTAS)
            for futuro in feitos:
                conta = pendentes.pop(futuro)
                executores.pop(futuro).shutdown(wait=False)
                try:
                    resultado = futuro.result()
                except Exception as erro:  # o processo morreu
                    resultado = {"conta": conta, "parou": f"{type(erro).__name__}: {erro}"[:300]}
                resultados.append(resultado)
                aviso(f"[{conta}] terminou: {resultado}")
                if resultado.get("bloqueio"):
                    agora = time.time()
                    bloqueios = [t for t in bloqueios if agora - t < JANELA_PARADA] + [agora]
                    if len(bloqueios) >= BLOQUEIOS_PARA_PARAR and not parada_pedida():
                        pedir_parada(f"{len(bloqueios)} contas barradas pelo Instagram em menos de 1 hora")
                        aviso(f"PARADA GERAL: {len(bloqueios)} contas barradas em menos de 1 hora; provavelmente o "
                              "Instagram limitou a conexão. Espere algumas horas antes de rodar de novo.")
                elif (_falha_passageira(resultado) and tentativas[conta] < TENTATIVAS and not parada_pedida()
                      and datetime.now(timezone.utc) < fim - timedelta(minutes=5)):
                    tentativas[conta] += 1
                    aviso(f"[{conta}] falha inesperada; tenta de novo em {REINICIAR // 60} min "
                          f"({tentativas[conta]} de {TENTATIVAS})")
                    iniciar(conta, REINICIAR)
            if procurar_novas and not parada_pedida() and datetime.now(timezone.utc) < fim - timedelta(minutes=5):
                for conta in procurar_novas(conhecidas):
                    iniciar(conta, random.uniform(30, 120))
    finally:
        for executor in executores.values():
            executor.shutdown(wait=False, cancel_futures=True)
    return resultados


def visitas_por_dia(horas: dict[str, float]) -> float:
    return sum(24 / prazo(h) for h in horas.values())
