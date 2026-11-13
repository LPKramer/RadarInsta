"""CLI do radar. Rode `python -m radar --help` para ver os comandos."""
import math
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import typer

from . import apify as apify_mod, app_dados, config, db, descoberta, enriquecimento, exportar as exp, mencoes
from . import influentes, monitoramento, municipios as mun, perfis, relatorio, score as sc, termos as tm

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

app = typer.Typer(help="Radar de reputação Energisa — banco de contas do Instagram (Tocantins).",
                  no_args_is_help=True, add_completion=False)


def _confirmar(custo: float, sim: bool) -> None:
    typer.echo(f"Custo estimado na Apify: até US$ {custo:.2f}")
    if not sim and not typer.confirm("Continuar?"):
        raise typer.Exit(1)


def _apify() -> apify_mod.Apify:
    try:
        return apify_mod.Apify()
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)


POSTS_POR_PAGINA_FEED = 12  # o mesmo de navegador.POSTS_POR_PAGINA, sem carregar o Playwright para estimar
OPCAO_VIA = typer.Option(None, "--via", help="apify (pago por resultado) ou navegador (o Edge logado no Instagram, "
                                              "sem custo). Padrão: RADAR_COLETA no .env.")


def _via(via: str | None) -> str:
    via = via or config.COLETA
    if via not in ("apify", "navegador"):
        typer.echo(f"--via deve ser apify ou navegador, não {via!r}.", err=True)
        raise typer.Exit(1)
    return via


@contextmanager
def _coletor(via: str, con=None, visivel: bool = config.NAV_VISIVEL, conta: str = "principal"):
    """Apify ou navegador. Uma interrupção do Instagram vira uma mensagem clara; o que já foi gravado fica."""
    if via == "apify":
        yield _apify()
        return
    from . import navegador

    try:
        with navegador.abrir(visivel, conta=conta) as coletor:
            yield coletor
    except (navegador.ColetaInterrompida, navegador.SemLogin) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)


def _tempo_navegador(leituras: int) -> str:
    """Duração aproximada: pausas médias entre leituras, pausas longas e o teto por hora."""
    pausa = sum(config.NAV_PAUSA) / 2 + sum(config.NAV_PAUSA_LONGA) / 2 / config.NAV_LONGA_A_CADA + 1
    horas = leituras / min(3600 / pausa, config.NAV_MAX_HORA)
    dias = leituras / config.NAV_MAX_DIA
    texto = f"{leituras} leituras, cerca de {horas:.1f} h"
    return texto + (f" (passa do teto diário: uns {dias:.1f} dias)" if dias > 1 else "")


@app.command("navegador-contas")
def navegador_contas():
    """Lista as contas do Instagram configuradas no navegador: logada, sem login, em uso agora ou desativada."""
    from . import navegador

    contas = navegador.contas_configuradas()
    if not contas:
        typer.echo("Nenhuma conta. Rode: python -m radar navegador-login")
    for conta in contas:
        estados = []
        for rede in sorted(navegador.redes_da_conta(conta)):
            sessao = navegador.tem_sessao(navegador.pasta_conta(conta), rede)
            estados.append(f"{rede}: " + ("logada" if sessao else "sem login" if sessao is False else "em uso agora"))
        motivo = navegador.desativada(conta)
        typer.echo(f"  {conta:15} {', '.join(estados)}" + (f"; DESATIVADA: {motivo}" if motivo else ""))


def _contas_do_navegador(contas: str | None) -> list[str]:
    """As contas pedidas (separadas por vírgula) ou todas as logadas, menos as desativadas."""
    from . import navegador

    nomes = [c.strip() for c in contas.split(",") if c.strip()] if contas else [
        c for c in navegador.contas_configuradas() if navegador.tem_sessao(navegador.pasta_conta(c)) is not False]
    for conta in [c for c in nomes if "instagram" not in navegador.redes_da_conta(c)]:
        typer.echo(f"{conta} fica de fora: é conta do {', '.join(sorted(navegador.redes_da_conta(conta)))}", err=True)
        nomes.remove(conta)
    for conta in [c for c in nomes if navegador.desativada(c)]:
        typer.echo(f"{conta} fica de fora, desativada: {navegador.desativada(conta)}", err=True)
        nomes.remove(conta)
    if not nomes:
        typer.echo("Nenhuma conta logada no navegador. Rode: python -m radar navegador-login", err=True)
        raise typer.Exit(1)
    return nomes


OPCAO_CONTAS = typer.Option(None, "--contas", help="Contas do navegador, separadas por vírgula, que trabalham em "
                                                    "paralelo (padrão: todas as logadas).")


@app.command("navegador-login")
def navegador_login(conta: str = typer.Option("principal", help="Nome da conta (cada uma tem o seu perfil do Edge; "
                                                                "use várias para coletar em paralelo)."),
                    rede: str = typer.Option("instagram", help="instagram, threads ou x. Conta nova criada para o "
                                                               "Threads ou o X fica só naquela rede.")):
    """Abre o Edge para você entrar na rede. A sessão fica em data/navegador/; a senha fica só no navegador."""
    from . import navegador
    from .redes import REDES

    if rede not in REDES:
        typer.echo(f"Rede desconhecida: {rede}. Use: {', '.join(REDES)}", err=True)
        raise typer.Exit(1)
    if not navegador.entrar(typer.echo, conta=conta, rede=rede):
        typer.echo("Login não concluído. Rode o comando de novo e entre na janela do Edge.", err=True)
        raise typer.Exit(1)


@app.command("navegador-abrir")
def navegador_abrir(conta: str = typer.Option("principal", help="Nome da conta.")):
    """Abre o Edge comum da conta no Instagram: para resolver uma verificação, recorrer de uma suspensão ou sair."""
    from . import navegador

    if not navegador.abrir_edge(conta, typer.echo):
        raise typer.Exit(1)


@app.command("piloto-rede")
def piloto_rede(rede: str = typer.Option("threads", help="threads ou x."),
                conta: str = typer.Option(..., help="A conta logada na rede (ex.: conta7 no Threads, x1 no X)."),
                termo: str = typer.Option("energisa", help="O termo da busca."),
                perfil: str = typer.Option(None, help="Um @ para ler o perfil também."),
                ver: Path = typer.Option(None, "--ver", help="Só mostra o resumo de um piloto já guardado.")):
    """Fase 3: uma busca (e um perfil) no Threads ou no X, com tudo o que o site mandou guardado em data/raw/, para
    escrever os leitores sobre amostras reais. No máximo duas leituras."""
    from . import navegador, redes

    if ver:
        for linha in redes.resumo_piloto(ver):
            typer.echo(linha)
        return
    if rede not in ("threads", "x") or rede not in navegador.redes_da_conta(conta):
        typer.echo(f"A conta {conta} não está no {rede}. Rode: python -m radar navegador-login --conta {conta} "
                   f"--rede {rede}", err=True)
        raise typer.Exit(1)
    try:
        caminho = redes.piloto(rede, navegador.pasta_conta(conta), termo, perfil)
    except redes.BloqueioRede as erro:
        typer.echo(str(erro), err=True)
        raise typer.Exit(2)
    typer.echo(f"Guardado em {caminho}")
    for linha in redes.resumo_piloto(caminho):
        typer.echo(linha)


@app.command("navegador-desativar")
def navegador_desativar(conta: str = typer.Argument(..., help="Nome da conta (veja navegador-contas)."),
                        motivo: str = typer.Option("desativada à mão", help="Anotação que aparece na lista.")):
    """Tira a conta da vigilância e dos comandos em paralelo, sem apagar o login. Volta com navegador-ativar."""
    from . import navegador

    navegador.desativar(conta, motivo)
    typer.echo(f"{conta} desativada. Se estiver na vigilância, sai em até 1 minuto (as outras seguem).")


@app.command("navegador-ativar")
def navegador_ativar(conta: str = typer.Argument(..., help="Nome da conta (veja navegador-contas).")):
    """Devolve ao trabalho uma conta desativada (por exemplo, depois que o Instagram reverteu a suspensão)."""
    from . import navegador

    if navegador.ativar(conta):
        typer.echo(f"{conta} ativa de novo; entra na próxima vigilância.")
    else:
        typer.echo(f"{conta} não estava desativada.")


@app.command("navegador-remover")
def navegador_remover(conta: str = typer.Argument(..., help="Nome da conta (veja navegador-contas)."),
                      sim: bool = typer.Option(False, "--sim", help="Não pede confirmação.")):
    """Apaga o login da conta (o perfil do Edge). O nome fica livre para outra conta do Instagram."""
    from . import navegador

    if not sim and not typer.confirm(f"Apagar o perfil do Edge da conta {conta} ({navegador.pasta_conta(conta)})?"):
        raise typer.Exit(1)
    try:
        apagados = navegador.remover(conta)
    except RuntimeError as erro:
        typer.echo(str(erro), err=True)
        raise typer.Exit(1)
    typer.echo(f"{conta} removida." if apagados else f"{conta} não tinha perfil.")


@app.command()
def municipios(offline: bool = typer.Option(False, help="Usa o CSV embutido em vez da API do IBGE.")):
    """Carrega os 139 municípios do Tocantins (IBGE) com população do Censo 2022."""
    con = db.conectar()
    if offline:
        registros = mun.ler_csv()
    else:
        try:
            registros = mun.baixar_ibge()
        except Exception as e:  # rede fora do ar, API mudou etc.
            typer.echo(f"IBGE indisponível ({e}); usando o CSV embutido.")
            registros = mun.ler_csv()
    n = mun.salvar(con, registros)
    portes = dict(con.execute("SELECT porte, COUNT(*) FROM municipios GROUP BY porte").fetchall())
    typer.echo(f"{n} municípios gravados. Por porte: {portes}")


@app.command()
def adicionar(usernames: list[str] = typer.Argument(..., help="@ das contas, com ou sem arroba."),
              municipio: str = typer.Option(None, help="Município que essas contas cobrem.")):
    """Inclui contas conhecidas pela equipe (não passam pelo pré-filtro)."""
    con = db.conectar()
    ibge_id = mun.casar_nome(municipio, mun.carregar(con)) if municipio else None
    if municipio and ibge_id is None:
        typer.echo(f"Município não encontrado: {municipio}", err=True)
        raise typer.Exit(1)
    novas = 0
    for u in usernames:
        u = u.strip().lstrip("@").lower()
        novas += perfis.ingerir_username(con, u, "manual")
        if ibge_id:
            db.vincular_municipio(con, u, ibge_id, "busca")
    con.commit()
    typer.echo(f"{len(usernames) - novas} de {len(usernames)} já estavam na base; {novas} contas novas.")
    if novas:
        typer.echo("Rode `enriquecer` para buscar os perfis das novas.")


@app.command()
def descobrir(municipio: str = typer.Option(None, help="Roda só para um município (piloto)."),
              dry_run: bool = typer.Option(False, "--dry-run", help="Só mostra termos e custo."),
              hashtags: bool = typer.Option(False, help="Também minera autores de hashtags locais."),
              google: bool = typer.Option(False, help="Também busca no Google com site:instagram.com."),
              google_estado: bool = typer.Option(False, help="Consultas Google estaduais, regionais e das cidades "
                                                             "T1 (portais que não levam o nome do município)."),
              paginas: int = typer.Option(1, help="Páginas de resultado por consulta Google (cada uma é cobrada)."),
              search_limit: int = typer.Option(config.SEARCH_LIMIT, help="Resultados por termo (1-250)."),
              por_hashtag: int = typer.Option(100, help="Posts lidos por hashtag."),
              busca: bool = typer.Option(True, "--busca/--sem-busca",
                                         help="Busca por termos (a fonte principal). --sem-busca roda só as extras."),
              cobertura_max: int = typer.Option(None, help="Só municípios com no máximo N contas A/B."),
              sim: bool = typer.Option(False, "--sim", "-y", help="Não pede confirmação.")):
    """Etapa 2: busca perfis no Instagram por termos de cada município."""
    con = db.conectar()
    try:
        alvo = descoberta.filtrar_municipios(mun.carregar(con), municipio)
        if cobertura_max is not None:
            alvo = descoberta.pouca_cobertura(con, alvo, cobertura_max)
        termos = descoberta.planejar_busca(con, alvo=alvo) if busca else []
    except (RuntimeError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    tags = tm.hashtags(alvo) if hashtags else []
    consultas = descoberta.planejar_google(con, alvo) if google else []
    if google_estado:
        consultas += descoberta.planejar_google_estado(con, mun.carregar(con))

    custo = descoberta.custo_busca(len(termos), search_limit)
    custo += descoberta.custo_hashtags(len(tags), por_hashtag) + descoberta.custo_google(len(consultas), paginas)
    typer.echo(f"{len(alvo)} municípios; {len(termos)} termos de busca novos"
               + (f", {len(tags)} hashtags" if tags else "")
               + (f", {len(consultas)} consultas Google ({paginas} página(s) cada)"
                  if google or google_estado else "") + ".")
    if dry_run:
        lista = termos + consultas
        for t in lista[:40]:
            typer.echo(f"  {t.termo}")
        if len(lista) > 40:
            typer.echo(f"  ... e mais {len(lista) - 40}")
        typer.echo(f"Custo estimado na Apify: até US$ {custo:.2f}")
        return
    if not (termos or tags or consultas):
        typer.echo("Nada novo para buscar.")
        return
    _confirmar(custo, sim)

    cliente = _apify()
    if termos:
        typer.echo(f"{descoberta.executar_busca(con, cliente, termos, search_limit, typer.echo)} resultados de busca.")
    if tags:
        typer.echo(f"{descoberta.executar_hashtags(con, cliente, tags, por_hashtag)} contas novas via hashtags.")
    if consultas:
        novas = descoberta.executar_google(con, cliente, consultas, typer.echo, paginas)
        typer.echo(f"{novas} contas novas via Google.")
    typer.echo(f"Pré-filtro: {dict(enriquecimento.prefiltrar(con))}")


@app.command()
def enriquecer(limite: int = typer.Option(None, help="Máximo de perfis a coletar."),
               via: str = OPCAO_VIA,
               dry_run: bool = typer.Option(False, "--dry-run"),
               sim: bool = typer.Option(False, "--sim", "-y")):
    """Etapa 3: coleta o perfil completo das contas que só têm o @ e aplica o pré-filtro."""
    con = db.conectar()
    via = _via(via)
    enriquecimento.prefiltrar(con)
    fila = enriquecimento.pendentes(con, limite)
    typer.echo(f"{len(fila)} perfis para coletar.")
    if via == "navegador":
        typer.echo(f"Pelo navegador: {_tempo_navegador(len(fila))}.")
    if dry_run or not fila:
        if via == "apify":
            typer.echo(f"Custo estimado na Apify: até US$ {enriquecimento.custo(len(fila)):.2f}")
        return
    if via == "apify":
        _confirmar(enriquecimento.custo(len(fila)), sim)
    with _coletor(via, con) as coletor:
        typer.echo(str(enriquecimento.enriquecer(con, coletor, limite, typer.echo)))


@app.command()
def classificar(direto: bool = typer.Option(False, help="Uma chamada por conta, sem batch (bom para piloto)."),
                limite: int = typer.Option(None, help="Máximo de contas."),
                reclassificar: bool = typer.Option(False, help="Refaz contas já classificadas."),
                nao_esperar: bool = typer.Option(False, help="Envia o batch e sai; rode de novo depois."),
                dry_run: bool = typer.Option(False, "--dry-run")):
    """Etapa 4: classifica as contas com IA (Claude ou DeepSeek, conforme RADAR_PROVEDOR_IA)."""
    import anthropic

    from . import classificacao as cl

    con = db.conectar()
    enriquecimento.prefiltrar(con)
    abertos = cl.lotes_abertos(con)
    contas = cl.pendentes(con, reclassificar, limite)
    typer.echo(f"{len(contas)} contas para classificar; {len(abertos)} lote(s) em andamento. "
               f"Modelo: {config.PROVEDOR_IA}/{config.MODELO}")
    if config.PROVEDOR_IA == "deepseek":
        _classificar_deepseek(con, contas, dry_run)
        return
    if dry_run:
        return

    try:
        _rodar_classificacao(con, anthropic.Anthropic(), abertos, contas, direto, limite, reclassificar, nao_esperar)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        typer.echo(f"A Anthropic recusou a credencial ({e.status_code}). Confira ANTHROPIC_API_KEY no .env.", err=True)
        raise typer.Exit(1)
    except TypeError as e:
        if "authentication" not in str(e):
            raise
        typer.echo("ANTHROPIC_API_KEY não configurada. Copie .env.example para .env e preencha.", err=True)
        raise typer.Exit(1)


def _classificar_deepseek(con, contas, dry_run) -> None:
    from . import classificacao_deepseek as ds

    typer.echo(f"Custo estimado na DeepSeek: até US$ {ds.custo(len(contas)):.2f}")
    if dry_run or not contas:
        return
    _com_deepseek(lambda cliente: ds.classificar(con, cliente, contas, aviso=typer.echo))


@app.command("testar-ia")
def testar_ia():
    """Confere se a DeepSeek responde daqui e se a chave vale (chamada grátis: só lista os modelos)."""
    import openai

    from . import classificacao_deepseek as ds

    try:
        modelos = ds.testar(ds.criar_cliente().with_options(timeout=20, max_retries=1))
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
        typer.echo(f"A rede está ok, mas a DeepSeek recusou a chave ({e.status_code}). Confira DEEPSEEK_API_KEY no "
                   ".env.", err=True)
        raise typer.Exit(1)
    typer.echo(f"DeepSeek ok. Modelos: {', '.join(modelos)}. Modelo usado pelo radar: {config.MODELO}.")


def _com_deepseek(tarefa) -> None:
    """Roda `tarefa(cliente)` e traduz os erros de credencial e de saldo. A tarefa devolve (ok, falhas)."""
    import openai

    from . import classificacao_deepseek as ds

    try:
        ok, falhas = tarefa(ds.criar_cliente())
    except RuntimeError as e:  # chave ausente ou saldo esgotado
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
        typer.echo(f"A DeepSeek recusou a credencial ({e.status_code}). Confira DEEPSEEK_API_KEY no .env.", err=True)
        raise typer.Exit(1)
    typer.echo(f"ok/falhas = ({ok}, {falhas})")


def _rodar_classificacao(con, cliente, abertos, contas, direto, limite, reclassificar, nao_esperar) -> None:
    from . import classificacao as cl

    for lote_id in abertos:
        if nao_esperar and cliente.messages.batches.retrieve(lote_id).processing_status != "ended":
            continue
        typer.echo(f"Lote {lote_id}: ok/falhas = {cl.processar_lote(con, cliente, lote_id, aviso=typer.echo)}")
    if cl.lotes_abertos(con):
        typer.echo("Ainda há lote em andamento; rode `classificar` de novo mais tarde.")
        return
    if abertos:
        contas = cl.pendentes(con, reclassificar, limite)
    if not contas:
        return
    if direto:
        typer.echo(f"ok/falhas = {cl.classificar_direto(con, cliente, contas, aviso=typer.echo)}")
        return
    lote_id = cl.enviar_lote(con, cliente, contas)
    typer.echo(f"Batch {lote_id} enviado com {len(contas)} contas.")
    if nao_esperar:
        typer.echo("Rode `classificar` de novo mais tarde para gravar os resultados.")
        return
    typer.echo(f"ok/falhas = {cl.processar_lote(con, cliente, lote_id, aviso=typer.echo)}")


@app.command()
def expandir(confianca: float = typer.Option(config.CONFIANCA_EXPANSAO, help="Confiança mínima da semente."),
             profundidade: int = typer.Option(config.PROFUNDIDADE_MAX, help="Passos máximos de bola de neve."),
             todos_os_tipos: bool = typer.Option(False, help="Usa também prefeituras, políticos e influenciadores "
                                                             "como sementes (por padrão, só portais e páginas)."),
             por_sinais: bool = typer.Option(False, "--por-sinais",
                                             help="Em vez de seguir as sementes, traz relacionados de qualquer conta "
                                                  "com sinal de notícia local: ligados a 2+ contas relevantes, ou com "
                                                  "palavra de notícia no @/nome e ligados a uma conta relevante ou a "
                                                  "um município do TO."),
             limite: int = typer.Option(None, help="Com --por-sinais: máximo de contas (as mais ligadas primeiro)."),
             min_ligacoes: int = typer.Option(2, help="Com --por-sinais: ligações a portais e páginas relevantes que "
                                                      "bastam sem palavra de notícia. Um número alto (ex.: 99) deixa "
                                                      "só os que têm palavra de notícia no @ ou no nome."),
             dry_run: bool = typer.Option(False, "--dry-run", help="Com --por-sinais: só lista e estima o custo.")):
    """Traz os perfis relacionados às contas relevantes (depois rode enriquecer e classificar)."""
    con = db.conectar()
    if por_sinais:
        candidatas = descoberta.candidatas_por_sinais(con, mun.carregar(con), min_ligacoes)[:limite]
        typer.echo(f"{len(candidatas)} relacionados com sinal de notícia local. "
                   f"Coleta dos perfis no enriquecer: até US$ {enriquecimento.custo(len(candidatas)):.2f}")
        if dry_run:
            for c in candidatas[:30]:
                typer.echo(f"  @{c['username']:30} {c['ligacoes']} ligações  {c['nome'] or ''}")
            return
        for c in candidatas:
            perfis.ingerir_username(con, c["username"], "relacionada", c["profundidade"])
        con.commit()
        typer.echo("Rode enriquecer e classificar.")
        return
    tipos = None if todos_os_tipos else descoberta.TIPOS_SEMENTE
    typer.echo(f"{descoberta.expandir(con, confianca, profundidade, tipos)} contas novas via perfis relacionados.")


@app.command()
def pontuar():
    """Etapa 5: calcula score e prioridade A/B/C."""
    typer.echo(f"Prioridades: {sc.pontuar(db.conectar())}")


@app.command()
def exportar(saida: Path = typer.Option(None, help="Caminho do .xlsx.")):
    """Gera a planilha de revisão (recalcula o score antes)."""
    con = db.conectar()
    sc.pontuar(con)
    caminho, n = exp.exportar(con, saida)
    typer.echo(f"{n} contas exportadas para {caminho}")


@app.command("importar-revisao")
def importar_revisao(arquivo: Path = typer.Argument(..., exists=True, help="Planilha revisada.")):
    """Grava no banco o status e a nota preenchidos na planilha."""
    typer.echo(str(exp.importar_revisao(db.conectar(), arquivo)))


@app.command()
def monitorar(limite: int = typer.Option(None, help="Máximo de contas (as de score mais alto primeiro)."),
              via: str = OPCAO_VIA,
              contas_nav: str = OPCAO_CONTAS,
              sem_stories: bool = typer.Option(False, "--sem-stories", help="Pelo navegador, não lê os stories."),
              dry_run: bool = typer.Option(False, "--dry-run"),
              sim: bool = typer.Option(False, "--sim", "-y")):
    """Fase 2: coleta os posts das contas monitoradas. Rode uma vez por dia.

    Pela Apify, vêm os 12 posts mais recentes de cada conta. Pelo navegador, cada conta é lida até o último post já
    guardado, sem esse limite; uma rodada interrompida retoma pelas contas que ficaram sem visita."""
    con = db.conectar()
    via = _via(via)
    contas = monitoramento.monitoradas(con, limite)
    typer.echo(f"{len(contas)} contas monitoradas.")
    if via == "navegador":
        nomes = _contas_do_navegador(contas_nav)
        # Uma leitura do feed por conta, mais o perfil de uma em cada sete (seguidores, uma vez por semana),
        # divididas entre as contas do Instagram que trabalham em paralelo.
        typer.echo(f"Pelo navegador, com {len(nomes)} conta(s): por conta, "
                   f"{_tempo_navegador(math.ceil(len(contas) * 8 / 7 / len(nomes)))}.")
        if dry_run or not contas:
            return
        if sem_stories:
            with _coletor(via, con) as coletor:
                typer.echo(str(monitoramento.coletar_novos(con, coletor, contas, typer.echo, com_stories=False)))
            return
        from . import paralelo

        paralelo.executar("monitorar", nomes, [c["username"] for c in contas], typer.echo)
        return
    if dry_run or not contas:
        typer.echo(f"Custo estimado na Apify: até US$ {monitoramento.custo(len(contas)):.2f}")
        return
    _confirmar(monitoramento.custo(len(contas)), sim)
    typer.echo(str(monitoramento.coletar(con, _apify(), contas, typer.echo)))


@app.command()
def historico(dias: int = typer.Option(60, help="Quantos dias para trás."),
              limite: int = typer.Option(None, help="Máximo de contas (as de score mais alto primeiro)."),
              max_usd: float = typer.Option(None, help="Teto de gasto na Apify: para de abrir lotes ao chegar nele."),
              via: str = OPCAO_VIA,
              dry_run: bool = typer.Option(False, "--dry-run"),
              sim: bool = typer.Option(False, "--sim", "-y")):
    """Fase 2: coleta todos os posts dos últimos N dias das contas monitoradas (o monitorar só traz os 12 últimos).

    Contas que já têm histórico cobrindo o período são puladas, então dá para rodar em partes."""
    con = db.conectar()
    desde = (date.today() - timedelta(days=dias)).isoformat()
    contas = monitoramento.sem_historico(con, monitoramento.monitoradas(con), desde)[:limite]
    estimativa = monitoramento.estimar_historico(con, contas, dias)
    custo = monitoramento.custo_historico(sum(estimativa.values()))
    typer.echo(f"{len(contas)} contas sem histórico desde {desde}; ~{sum(estimativa.values()):,} posts.".replace(",", "."))
    via = _via(via)
    if via == "navegador":
        # Cada rolagem da grade do perfil traz 12 posts.
        paginas = sum(max(1, math.ceil(n / POSTS_POR_PAGINA_FEED)) for n in estimativa.values())
        typer.echo(f"Pelo navegador: {_tempo_navegador(paginas)}.")
    elif dry_run or not contas:
        typer.echo(f"Custo estimado na Apify: até US$ {custo:.2f}")
    if dry_run or not contas:
        return
    if via == "apify":
        _confirmar(custo if max_usd is None else min(custo, max_usd), sim)
    with _coletor(via, con) as coletor:
        resultado = monitoramento.coletar_historico(con, coletor, contas, desde, estimativa, max_usd, typer.echo)
    typer.echo(f"{resultado}. Depois rode mencoes e exportar-app.")


@app.command("stories")
def stories_(limite: int = typer.Option(None, help="Máximo de contas monitoradas."),
             contas_nav: str = OPCAO_CONTAS,
             dry_run: bool = typer.Option(False, "--dry-run")):
    """Lê os stories de todas as contas monitoradas (somem em 24 horas), com as contas do navegador em paralelo.

    Abre a página de stories de cada conta (uma leitura), lê o texto (legenda automática, adesivos e OCR da imagem),
    grava os novos e salva em data/stories/ a mídia dos que falam de energia. Depois, rode mencoes."""
    from . import paralelo

    con = db.conectar()
    contas = monitoramento.monitoradas(con, limite)
    nomes = _contas_do_navegador(contas_nav)
    typer.echo(f"{len(contas)} contas monitoradas; por conta do navegador, "
               f"{_tempo_navegador(math.ceil(len(contas) / len(nomes)))}.")
    if dry_run or not contas:
        return
    paralelo.executar("stories", nomes, [c["username"] for c in contas], typer.echo)
    typer.echo("Depois rode: python -m radar mencoes")


@app.command()
def vigiar(horas: float = typer.Option(8, help="Por quanto tempo fica rodando (ex.: 8 = a madrugada toda)."),
           contas_nav: str = OPCAO_CONTAS,
           sem_ia: bool = typer.Option(False, "--sem-ia", help="Não classifica na hora (deixa para o mencoes)."),
           dry_run: bool = typer.Option(False, "--dry-run", help="Só mostra prioridades e capacidade."),
           volta: bool = typer.Option(False, "--volta", help="Uma volta: passa uma vez por toda página sem visita nas "
                                                            "últimas RADAR_VIGIA_VOLTA_H horas (4) e termina, a "
                                                            "RADAR_VIGIA_VOLTA_POR_HORA leituras por hora por conta "
                                                            "(60); --horas vira o limite. O agendar.ps1 faz duas por "
                                                            "dia."),
           parar: bool = typer.Option(False, "--parar", help="Pede para a vigilância em andamento parar (cada conta "
                                                            "termina a visita atual).")):
    """Vigilância contínua: visita primeiro as páginas mais atrasadas, por prioridade, com as contas em paralelo.

    Influenciadores e páginas de fofoca a cada 1 h, portais e páginas que já falaram de energia ou têm muitos seguidores
    a cada 2 h, os demais portais a cada 6 h, o resto uma vez por dia (ajuste em RADAR_VIGIA_*_H). As contas entram com
    alguns minutos de diferença, contas logadas depois entram sozinhas, e duas contas barradas pelo Instagram em menos
    de 1 hora param todas. Posts novos que passam no filtro vão na hora para a IA, e as menções negativas entram em
    data/alertas.txt. Com --volta, passa uma vez por todas as páginas e termina."""
    from collections import Counter

    from . import vigia

    if parar:
        vigia.pedir_parada("pedido pelo comando vigiar --parar")
        typer.echo("Pedido de parada registrado: cada conta para em até 1 minuto (ou ao fim da visita atual).")
        return
    con = db.conectar()
    horas_por_conta = vigia.intervalos(con)
    nomes = _contas_do_navegador(contas_nav)
    if volta:
        inicio = datetime.now(timezone.utc)
        faltam = len(vigia.atrasadas(con, inicio, horas_por_conta, volta=True))
        leituras = faltam * vigia.LEITURAS_POR_PAGINA_NA_VOLTA / len(nomes)  # por conta
        por_hora = min(config.VIGIA_VOLTA_POR_HORA, config.NAV_MAX_HORA)
        typer.echo(f"Volta: {faltam} de {len(horas_por_conta)} páginas sem visita nas últimas "
                   f"{config.VIGIA_VOLTA_H:g} h, ~{leituras * len(nomes):.0f} leituras, ~{leituras:.0f} por conta com "
                   f"{len(nomes)} conta(s) do navegador. A {por_hora:g} leituras por hora por conta, "
                   f"~{f'{leituras / por_hora:.1f}'.replace('.', ',')} h (teto: {config.NAV_MAX_HORA} por hora e "
                   f"{config.NAV_MAX_DIA} por dia).")
        if dry_run:
            return
        con.close()
        vigia.executar(nomes, horas, com_ia=not sem_ia, aviso=typer.echo, volta=True)
        con = db.conectar()
        faltam = len(vigia.atrasadas(con, datetime.now(timezone.utc), vigia.intervalos(con), volta=True,
                                     inicio=inicio))
        minutos = (datetime.now(timezone.utc) - inicio).total_seconds() / 60
        typer.echo(f"Volta completa em {minutos:.0f} min." if not faltam else
                   f"Volta incompleta em {minutos:.0f} min: {faltam} páginas ficaram sem visita (limite de horas, "
                   "parada ou contas barradas).")
        return
    por_prioridade = Counter(horas_por_conta.values())
    typer.echo("Contas por intervalo: " + ", ".join(f"{n} a cada {h:g} h" for h, n in sorted(por_prioridade.items())))
    visitas = vigia.visitas_por_dia(horas_por_conta)
    # Cada visita custa ~1,3 leitura (rolagem de quem publica muito e página de stories quando há story novo).
    leituras = visitas * 1.3
    typer.echo(f"No máximo ~{visitas:.0f} visitas por dia, ~{leituras:.0f} leituras (páginas sem novidade são "
               f"visitadas menos). Com {len(nomes)} conta(s) do navegador: "
               f"~{leituras / len(nomes):.0f} por conta (teto: {config.NAV_MAX_DIA}). "
               f"Contas necessárias para dar conta: {math.ceil(leituras / config.NAV_MAX_DIA)}.")
    if dry_run:
        return
    vigia.executar(nomes, horas, com_ia=not sem_ia, aviso=typer.echo,
                   procurar_novas=None if contas_nav else vigia.contas_novas)


@app.command("mencoes")
def mencoes_(reanalisar: bool = typer.Option(False, help="Refaz posts já analisados."),
             desde: str = typer.Option(None, help="Só posts publicados a partir de AAAA-MM-DD."),
             limite: int = typer.Option(None, help="Máximo de posts."),
             completar_setor: bool = typer.Option(False, "--completar-setor",
                                                  help="Reanalisa as menções antigas que ainda não têm setor."),
             dry_run: bool = typer.Option(False, "--dry-run")):
    """Fase 2: separa os posts sobre energia/Energisa e classifica tema, setor da Energisa, sentimento e alcance."""
    con = db.conectar()
    posts = mencoes.candidatos(con, reanalisar, desde, limite, sem_setor=completar_setor)
    typer.echo(f"{len(posts)} posts com palavras-chave de energia para analisar.")
    typer.echo(f"Custo estimado na DeepSeek: até US$ {mencoes.custo(len(posts)):.2f}")
    if dry_run or not posts:
        return
    _exigir_deepseek()
    _com_deepseek(lambda cliente: mencoes.analisar(con, cliente, posts, aviso=typer.echo))
    _consolidar_fatos(con)


def _consolidar_fatos(con) -> None:
    """A mesma notícia (fatos.py) para as menções que acabaram de ser analisadas. Centavos; sem IA, fica para depois."""
    from . import classificacao_deepseek as ds, fatos

    try:
        fatos.consolidar(con, ds.criar_cliente(), aviso=typer.echo)
    except Exception as erro:  # sem chave, sem saldo ou sem conexão: o próximo comando (ou a vigilância) consolida
        typer.echo(f"Mesma notícia: ficou para depois ({type(erro).__name__}).", err=True)


@app.command("fatos")
def fatos_(refazer: bool = typer.Option(False, "--refazer", help="Desfaz os agrupamentos (menos os corrigidos à mão) "
                                                                    "e agrupa de novo."),
           desde: str = typer.Option(None, help="Só menções publicadas a partir de AAAA-MM-DD."),
           ver: int = typer.Option(10, help="Quantos agrupamentos maiores mostrar no fim."),
           dry_run: bool = typer.Option(False, "--dry-run", help="Só mostra o que falta e o custo.")):
    """A mesma notícia: junta as menções (posts, stories e matérias) que tratam do mesmo fato. Primeiro pelo link em
    comum, depois pela semelhança do resumo; nos casos parecidos, a DeepSeek decide. A ficha do post mostra o
    agrupamento ("a mesma notícia em N perfis") e o relatório ganha a aba "Mesma notícia"."""
    from . import classificacao_deepseek as ds, fatos

    con = db.conectar()
    pendentes = fatos.pendentes(con, desde)
    typer.echo(f"Situação: {fatos.situacao(con)}")
    typer.echo(f"{len(pendentes)} menções sem agrupamento. Custo estimado na DeepSeek: até US$ "
               f"{fatos.custo(len(pendentes)):.2f}")
    if not dry_run and (pendentes or refazer):
        _exigir_deepseek()
        cliente = ds.criar_cliente()
        resumo = (fatos.refazer if refazer else fatos.consolidar)(con, cliente, desde=desde, aviso=typer.echo)
        typer.echo(f"Resultado: {dict(resumo)}")
    for f in fatos.maiores(con, ver):
        typer.echo(f"  #{f['id']}: {f['posts']} posts de {f['perfis']} perfis, {f['primeira']['data'][:10]} a "
                   f"{f['ultima']['data'][:10]}, {f['interacoes']} interações · {f['titulo'][:90]}")


@app.command("fatos-revisar")
def fatos_revisar(dias: float = typer.Option(None, help="Só pares com um agrupamento desses últimos dias (sem: "
                                                        "todos)."),
                  sem_ia: bool = typer.Option(False, "--sem-ia", help="Só junta os pares óbvios pelo texto."),
                  dry_run: bool = typer.Option(False, "--dry-run", help="Só conta os pares e o custo.")):
    """Junta os agrupamentos da mesma notícia que ficaram separados: os quase iguais pelo texto (ou o post e o story
    do mesmo perfil no mesmo dia), sem IA; os outros pares parecidos, a DeepSeek decide. Cada decisão fica gravada e
    não é perguntada de novo. A vigilância faz o mesmo com os agrupamentos dos últimos 3 dias."""
    from datetime import datetime, timezone

    from . import classificacao_deepseek as ds, fatos

    con = db.conectar()
    previa = fatos.revisar(con, dias=dias, dry_run=True)
    duvidas = previa["pares"] - previa["obvios"]
    typer.echo(f"{previa['pares']} pares parecidos: {previa['obvios']} óbvios (sem IA) e {duvidas} para a DeepSeek "
               f"(até US$ {fatos.custo(duvidas):.2f}).")
    if dry_run:
        return
    if not fatos.reservar_vez(con, "revisao", datetime.now(timezone.utc)):
        typer.echo("A vigilância está consolidando agora; tente de novo em alguns minutos.", err=True)
        raise typer.Exit(1)
    try:
        cliente = None
        if not sem_ia and duvidas:
            _exigir_deepseek()
            cliente = ds.criar_cliente()
        resumo = fatos.revisar(con, cliente, dias=dias, aviso=typer.echo)
    finally:
        fatos.liberar_vez(con)
    typer.echo(f"Resultado: {dict(resumo)}")
    typer.echo(f"Situação: {fatos.situacao(con)}")


@app.command("estimar-stories")
def estimar_stories(dias: float = typer.Option(None, help="Só os stories dos últimos tantos dias (sem: todos).")):
    """Interações estimadas dos stories sobre energia: a mediana dos posts de feed da conta mais próximos no tempo,
    com teto de 5% dos seguidores. A vigilância recalcula os dos últimos 30 dias a cada 10 minutos."""
    from . import estimativa

    con = db.conectar()
    mudaram = estimativa.atualizar(con, dias)
    linhas = con.execute("SELECT COUNT(*), COUNT(interacoes_estimadas), SUM(interacoes_estimadas) FROM posts p "
                         "JOIN mencoes x ON x.rede = p.rede AND x.post_id = p.post_id AND x.sobre_energia = 1 "
                         "WHERE p.rede = 'instagram' AND p.tipo = 'Story'").fetchone()
    typer.echo(f"{mudaram} estimativas novas ou mudadas. Stories sobre energia: {linhas[0]}, com estimativa: "
               f"{linhas[1]}, somando {linhas[2] or 0} interações estimadas.")


@app.command("fatos-mover")
def fatos_mover(post: str = typer.Option(..., help="post_id da menção (a rede web usa o id da matéria)."),
                para: str = typer.Option(..., help="Número do agrupamento de destino, ou 'novo'."),
                rede: str = typer.Option("instagram", help="instagram ou web.")):
    """Corrige à mão o agrupamento de uma menção. A correção fica mesmo depois de um `fatos --refazer`."""
    from . import fatos

    con = db.conectar()
    try:
        fato_id = fatos.mover(con, rede, post, None if para == "novo" else int(para))
    except ValueError as erro:
        typer.echo(str(erro), err=True)
        raise typer.Exit(1)
    typer.echo(f"{rede}:{post} agora está no agrupamento #{fato_id}: {fatos.resumo(con, fato_id)['titulo']}")


@app.command("radios-propor")
def radios_propor(descobrir: bool = typer.Option(False, "--descobrir", help="Busca no Instagram as rádios da lista "
                                                                            "curada e 'rádio <cidade>' (gasta leituras)."),
                  orcamento: int = typer.Option(150, help="Máximo de leituras da descoberta."),
                  conta: str = typer.Option("principal", help="Conta do navegador para a descoberta (fora da "
                                                              "vigilância)."),
                  dry_run: bool = typer.Option(False, "--dry-run", help="Só conta as candidatas.")):
    """Rádios do TO: levanta as contas de emissora, programa e radialista, a DeepSeek classifica, e grava a planilha
    data/radios_propostas.xlsx para o usuário marcar a coluna "aprovar". Depois: radar radios-aprovar --planilha."""
    from . import radios

    con = db.conectar()
    if descobrir and not dry_run:
        with _coletor("navegador", con, conta=conta) as coletor:
            typer.echo(f"Descoberta: {dict(radios.descobrir(con, coletor, orcamento, typer.echo))}")
    candidatas = radios.candidatas(con)
    pendentes = radios.nao_classificadas(con, candidatas)
    typer.echo(f"{len(candidatas)} contas com cara de rádio; {len(pendentes)} ainda sem classificação "
               f"(até US$ {ds_custo(len(pendentes)):.2f} na DeepSeek).")
    if dry_run:
        return
    if pendentes:
        _exigir_deepseek()
        from . import classificacao_deepseek as ds

        typer.echo(f"Classificação: {dict(radios.classificar(con, ds.criar_cliente(), pendentes, aviso=typer.echo))}")
    caminho, n = radios.propor(con)
    typer.echo(f"{n} rádios, programas e radialistas em {caminho}. Marque 'sim' na coluna 'aprovar' e rode: "
               "python -m radar radios-aprovar --planilha")


def ds_custo(n: int) -> float:
    from . import classificacao_deepseek as ds

    return ds.custo(n)


@app.command("radios-aprovar")
def radios_aprovar(planilha: bool = typer.Option(False, "--planilha", help="Lê a coluna 'aprovar' de "
                                                                          "data/radios_propostas.xlsx."),
                   contas: str = typer.Option("", help="Ou as contas, separadas por vírgula.")):
    """Grava as rádios aprovadas na lista radios_to: a vigilância passa a visitá-las (no mínimo a cada 6 h) e os
    vídeos delas entram na transcrição. A lista é substituída pela nova escolha."""
    from . import radios

    escolhidas = radios.aprovadas_na_planilha() if planilha else [c for c in contas.split(",") if c.strip()]
    if not escolhidas:
        typer.echo("Nenhuma conta aprovada: marque 'sim' na planilha ou passe --contas.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Lista radios_to: {radios.aprovar(db.conectar(), escolhidas)} contas.")


@app.command("radios-lista")
def radios_lista():
    """As rádios aprovadas (lista radios_to)."""
    from . import radios

    con = db.conectar()
    for u in sorted(radios.da_lista(con)):
        typer.echo(f"  @{u}")


@app.command("transcrever")
def transcrever(horas: float = typer.Option(24, help="Por quanto tempo fica olhando a fila (a cada 5 min)."),
                uma_vez: bool = typer.Option(False, "--uma-vez", help="Uma passada pela fila e termina."),
                limite: int = typer.Option(10, help="Máximo de vídeos na passada (com --uma-vez)."),
                modelo: str = typer.Option(config.TRANSCRICAO_MODELO, help="small (rápido) ou medium (melhor e "
                                                                           "~3x mais lento)."),
                sem_ia: bool = typer.Option(False, "--sem-ia", help="Só transcreve; a análise fica para depois."),
                sem_ao_vivo: bool = typer.Option(False, "--sem-ao-vivo", help="Não grava as rádios ao vivo."),
                dry_run: bool = typer.Option(False, "--dry-run", help="Só mostra a fila.")):
    """Vídeos das rádios (lista radios_to) e jornais da manhã ao vivo (radios_ao_vivo.csv, 6h às 9h): grava,
    transcreve no PC e manda para a análise a fala que toca em energia; a menção negativa vira alerta. Roda numa
    janela própria (transcrever.ps1), ao lado da vigilância, que enche a fila dos vídeos. O modelo, o áudio
    passageiro e as transcrições completas ficam em RADAR_MIDIA_DIR."""
    from datetime import datetime, timezone

    from . import transcricao, vigia

    con = db.conectar()
    situacao = transcricao.situacao(con)
    typer.echo(f"Fila: {situacao['estados'] or 'vazia'}; ~{situacao['minutos_na_fila']:.0f} min de áudio à espera, "
               f"{situacao['minutos_no_dia']:.0f} de {config.TRANSCRICAO_MINUTOS_DIA:.0f} min nas últimas 24 h; "
               f"{situacao['com_energia']} vídeos com fala sobre energia. Pasta: {config.MIDIA_DIR}")
    if dry_run:
        return
    ia = None
    if not sem_ia:
        _exigir_deepseek()
        if vigia.ia_disponivel():
            from . import classificacao_deepseek as ds

            ia = ds.criar_cliente()
        else:
            typer.echo("A DeepSeek não respondeu: transcrevo, e a análise fica para o comando mencoes.", err=True)
    transcricao.prioridade_baixa()
    transcritor = transcricao.Transcritor(modelo)
    if uma_vez:
        typer.echo(f"{dict(+transcricao.rodar(con, ia, transcritor, limite, typer.echo))}")
        return
    ate = datetime.now(timezone.utc) + timedelta(hours=horas)
    try:
        typer.echo(f"{dict(+transcricao.vigiar(con, ia, transcritor, ate, typer.echo, ao_vivo=not sem_ao_vivo))}")
    except KeyboardInterrupt:
        typer.echo("parando a pedido")


@app.command("ao-vivo-testar")
def ao_vivo_testar(segundos: int = typer.Option(15, help="Segundos gravados de cada rádio.")):
    """Grava alguns segundos do sinal de cada rádio de radar/dados/radios_ao_vivo.csv, para conferir os endereços.
    Nada vai para o banco."""
    from . import aovivo

    resultado = aovivo.testar(segundos, typer.echo)
    typer.echo(f"{sum(resultado.values())} de {len(resultado)} rádios com sinal.")


@app.command("triar-stories")
def triar_stories(desde: str = typer.Option(None, help="Só stories gravados a partir de AAAA-MM-DD."),
                  limite: int = typer.Option(None, help="Máximo de stories."),
                  dry_run: bool = typer.Option(False, "--dry-run", help="Só conta, sem chamar a IA.")):
    """Triagem pela IA, 20 stories por pedido, dos stories guardados que não passaram no filtro de palavras. Os
    marcados vão para a análise completa das menções (e, se negativos, para os alertas). Na vigilância isso já
    acontece a cada página visitada; este comando pega os anteriores e os que ficaram sem triagem."""
    import openai

    from . import classificacao_deepseek as ds, triagem, vigia

    con = db.conectar()
    pendentes = triagem.pendentes(con, desde)
    pendentes = pendentes[:limite] if limite else pendentes
    typer.echo(f"{len(pendentes)} stories com texto para a triagem, em {math.ceil(len(pendentes) / triagem.LOTE)} "
               "pedidos.")
    if dry_run or not pendentes:
        return
    _exigir_deepseek()
    try:
        resumo = triagem.rodar(con, ds.criar_cliente(), pendentes, aviso=typer.echo)
    except RuntimeError as e:  # sem conexão ou sem saldo
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
        typer.echo(f"A DeepSeek recusou a credencial ({e.status_code}). Confira DEEPSEEK_API_KEY no .env.", err=True)
        raise typer.Exit(1)
    n = max(resumo["triados"], 1)
    typer.echo(f"Triagem: {dict(resumo)}")
    typer.echo(f"Por story: {resumo['entrada'] / n:.0f} tokens de entrada ({resumo['cache'] / n:.0f} em cache) e "
               f"{resumo['saida'] / n:.1f} de saída.")
    marcados = triagem.marcados_sem_analise(con)
    typer.echo(f"{len(marcados)} stories marcados para a análise completa (até US$ {mencoes.custo(len(marcados)):.2f}).")
    if marcados:
        _com_deepseek(lambda cliente: mencoes.analisar(con, cliente, marcados, aviso=typer.echo))
        typer.echo(f"{vigia.alertar(con, marcados, typer.echo)} alertas de menção negativa em data/alertas.txt.")
        sobre = sum(1 for p in marcados if con.execute(
            "SELECT sobre_energia FROM mencoes WHERE rede = ? AND post_id = ?", (p["rede"], p["post_id"])).fetchone()[0])
        typer.echo(f"Confirmados pela análise completa como sobre energia: {sobre} de {len(marcados)}.")
        _consolidar_fatos(con)


@app.command("noticias")
def noticias_(dias: int = typer.Option(90, help="Só matérias dos últimos N dias (0 = sem limite)."),
              sem_ia: bool = typer.Option(False, "--sem-ia", help="Só busca e grava; a IA fica para depois."),
              dry_run: bool = typer.Option(False, "--dry-run", help="Só mostra a situação, sem buscar.")):
    """Matérias de sites de notícia sobre energia e a Energisa no TO, pelo Google Notícias (grátis, sem conta).
    Entram na base de menções como rede 'web'; a IA analisa as que passam no filtro de palavras-chave."""
    from . import noticias

    con = db.conectar()
    typer.echo(f"Antes: {noticias.situacao(con)}")
    if dry_run:
        return
    typer.echo(f"Busca: {noticias.coletar(con, dias or None, typer.echo)}")
    posts = noticias.candidatos(con)
    typer.echo(f"{len(posts)} matérias com palavras-chave de energia para analisar. "
               f"Custo estimado na DeepSeek: até US$ {mencoes.custo(len(posts)):.2f}")
    if posts and not sem_ia:
        _exigir_deepseek()
        _com_deepseek(lambda cliente: mencoes.analisar(con, cliente, posts, aviso=typer.echo))
        typer.echo(f"{noticias.alertas(con, posts, typer.echo)} alertas de matéria negativa em data/alertas.txt.")
        _consolidar_fatos(con)
    typer.echo(f"Depois: {noticias.situacao(con)}")


@app.command("sites-descobrir")
def sites_descobrir(reverificar: bool = typer.Option(False, "--reverificar", help="Procura de novo o feed de todos."),
                    dry_run: bool = typer.Option(False, "--dry-run", help="Só conta os candidatos, sem acessar.")):
    """Monta a base de sites de notícia (curadoria, veículos do Google Notícias e links da bio das contas de notícia,
    rádio e jornalista do TO) e acha o feed de cada um. Grátis; um a oito pedidos por site."""
    from . import sites

    con = db.conectar()
    base = sites.base_candidata(con)
    typer.echo(f"{len(base)} sites candidatos: {dict(Counter(s['origem'] for s in base.values()))}")
    if dry_run:
        return
    typer.echo(f"Descoberta: {dict(sites.descobrir(con, typer.echo, reverificar=reverificar))}")
    typer.echo(f"Base: {sites.situacao(con)}")


@app.command("sites")
def sites_(dias: int = typer.Option(14, help="Só matérias dos últimos N dias."),
           sem_ia: bool = typer.Option(False, "--sem-ia", help="Só lê e grava; a IA fica para depois."),
           dry_run: bool = typer.Option(False, "--dry-run", help="Só mostra a base.")):
    """Lê agora o feed dos sites da base e grava as matérias de energia (a vigilância faz isso a cada hora). A IA
    analisa as pendentes e a matéria negativa vira alerta."""
    from . import noticias, sites

    con = db.conectar()
    typer.echo(f"Base: {sites.situacao(con)}")
    if dry_run:
        return
    typer.echo(f"Leitura: {dict(sites.coletar(con, typer.echo, dias=dias))}")
    posts = noticias.candidatos(con)
    typer.echo(f"{len(posts)} matérias com palavras-chave de energia para analisar (até US$ "
               f"{mencoes.custo(len(posts)):.2f} na DeepSeek).")
    if posts and not sem_ia:
        _exigir_deepseek()
        _com_deepseek(lambda cliente: mencoes.analisar(con, cliente, posts, aviso=typer.echo))
        typer.echo(f"{noticias.alertas(con, posts, typer.echo)} alertas de matéria negativa em data/alertas.txt.")
        _consolidar_fatos(con)


@app.command()
def reacoes(posts: int = typer.Option(20, help="Posts (os de mais comentários) que terão os comentários lidos."),
            por_post: int = typer.Option(None, help="Comentários lidos por post (padrão: RADAR_COMENTARIOS_POR_POST "
                                                    "pelo navegador, 15 pela Apify)."),
            via: str = typer.Option("navegador", "--via", help="navegador (grátis) ou apify (US$ 0,0026 por "
                                                               "comentário)."),
            conta: str = typer.Option("principal", help="Conta do navegador (uma que não esteja na vigilância)."),
            sem_ia: bool = typer.Option(False, "--sem-ia", help="Só lê e grava; a análise fica para depois."),
            dry_run: bool = typer.Option(False, "--dry-run"),
            sim: bool = typer.Option(False, "--sim", "-y")):
    """Lê os comentários dos posts sobre energia e dos que citam a Energisa (sem guardar quem comentou). A DeepSeek
    dá o sentimento, o relato e o local de cada comentário, resume a reação de cada post, e vários relatos de falta
    de energia no mesmo lugar viram alerta. A vigilância já faz isso sozinha com as menções novas."""
    from . import comentarios

    con = db.conectar()
    via = _via(via)
    por_post = por_post or (config.COMENTARIOS_POR_POST if via == "navegador" else 15)
    fila = mencoes.sem_comentarios(con, posts)
    leituras = sum(1 + min(p["comentarios"] or 0, por_post) // 10 for p in fila)
    typer.echo(f"{len(fila)} posts para ler comentários (até {por_post} cada).")
    if dry_run:
        if via == "apify":
            typer.echo(f"Custo estimado na Apify: até US$ {monitoramento.custo_comentarios(len(fila), por_post):.2f}")
        else:
            typer.echo(f"Pelo navegador, no máximo: {_tempo_navegador(leituras)}.")
        return
    if fila:
        if via == "apify":
            _confirmar(monitoramento.custo_comentarios(len(fila), por_post), sim)
        with _coletor(via, con, conta=conta) as coletor:
            if via == "apify":
                n = monitoramento.coletar_comentarios(con, coletor, fila, por_post, typer.echo)
                mencoes.marcar_comentarios_lidos(con, fila)
            else:  # post a post, marcando na hora: a vigilância, rodando junto, não lê o mesmo post
                n = 0
                for i, post in enumerate(fila, 1):
                    lido = con.execute("SELECT n_comentarios_lidos FROM mencoes WHERE rede = ? AND post_id = ?",
                                       (post["rede"], post["post_id"])).fetchone()
                    if lido and lido[0] is not None:
                        continue
                    novos = monitoramento.coletar_comentarios(con, coletor, [post], por_post, lambda _: None)
                    mencoes.marcar_comentarios_lidos(con, [post])
                    n += novos
                    typer.echo(f"{i}/{len(fila)} @{post['username']}: {novos} comentários")
        typer.echo(f"{n} comentários gravados.")
    if sem_ia:
        return
    a_analisar = comentarios.pendentes(con)
    pendentes = mencoes.reacao_pendente(con)
    if a_analisar or pendentes:
        _exigir_deepseek()
    if a_analisar:
        from . import classificacao_deepseek as ds

        typer.echo(f"Analisando {len(a_analisar)} comentários (sentimento, relato e local)...")
        try:
            resumo = comentarios.rodar(con, ds.criar_cliente(), aviso=typer.echo)
        except RuntimeError as e:  # sem chave ou sem saldo
            typer.echo(str(e), err=True)
            raise typer.Exit(1)
        typer.echo(f"Comentários: {dict(resumo)}")
        comentarios.alertas_de_relatos(con, aviso=typer.echo)
    pendentes = mencoes.reacao_pendente(con)
    if pendentes:
        typer.echo(f"Analisando a reação em {len(pendentes)} posts...")
        _com_deepseek(lambda cliente: mencoes.analisar_reacoes(con, cliente, pendentes, aviso=typer.echo))


@app.command()
def sentimento(dias: int = typer.Option(30, help="Posts publicados nos últimos N dias.")):
    """A noção de sentimento nos comentários: negativos, positivos e neutros, os relatos, os lugares com mais relatos
    de falta de energia, a semana a semana e os posts com mais comentários negativos."""
    from . import comentarios

    r = comentarios.resumo(db.conectar(), dias)
    if not r["comentarios"]:
        typer.echo("Nenhum comentário analisado no período. Rode: python -m radar reacoes")
        return
    typer.echo(f"{r['comentarios']} comentários analisados nos posts dos últimos {dias} dias: "
               f"{r['negativos_pct']}% negativos, {r['positivos_pct']}% positivos, {r['neutros_pct']}% neutros.")
    typer.echo(f"Relatos: {r['relatos']}")
    typer.echo(f"Lugares com mais relatos de falta de energia: {r['lugares']}")
    for semana, c in r["semanas"].items():
        n = sum(c.values())
        typer.echo(f"  semana de {semana[8:10]}/{semana[5:7]}: {n} comentários, "
                   f"{round(100 * c.get('negativo', 0) / n)}% negativos")
    typer.echo("Posts com mais comentários negativos:")
    for p in r["posts_mais_negativos"]:
        typer.echo(f"  @{p['conta']}: {p['negativos']}  {p['url']}")


def _exigir_deepseek() -> None:
    if config.PROVEDOR_IA != "deepseek":
        typer.echo("A análise de menções por enquanto só roda com a DeepSeek. Configure DEEPSEEK_API_KEY e "
                   "RADAR_PROVEDOR_IA=deepseek no .env.", err=True)
        raise typer.Exit(1)


@app.command("gabarito-rodar")
def gabarito_rodar(planilha: Path = typer.Option(None, help="Planilha do gabarito (padrão: "
                                                             "DemoDay/gabarito/Gabarito_Radar_v0.xlsx)."),
                   dry_run: bool = typer.Option(False, "--dry-run", help="Só conta os casos, sem chamar a IA.")):
    """Passa cada caso do gabarito pela análise das menções, num pedido isolado, e guarda as respostas num arquivo
    à parte, para o autor marcar a resposta certa sem ver a da IA. Nada é gravado no banco."""
    from . import gabarito

    casos = gabarito.ler_casos(planilha or gabarito.PLANILHA)
    typer.echo(f"{len(casos)} casos; custo estimado na DeepSeek: até US$ {mencoes.custo(len(casos)):.2f}")
    if dry_run or not casos:
        return
    _exigir_deepseek()
    con = db.conectar()
    feito = {}

    def tarefa(cliente):
        feito["respostas"] = gabarito.rodar(con, cliente, casos, aviso=typer.echo)
        return len(feito["respostas"]), len(casos) - len(feito["respostas"])

    _com_deepseek(tarefa)
    caminho = gabarito.salvar_respostas(feito["respostas"])
    typer.echo(f"Respostas guardadas em {caminho}. Não abra antes de marcar a planilha; depois rode: "
               "python -m radar gabarito-conferir")


@app.command("gabarito-conferir")
def gabarito_conferir(planilha: Path = typer.Option(None, help="Planilha do gabarito, já marcada.")):
    """Compara a resposta marcada pelo autor com a da IA e escreve a planilha do resultado, caso a caso."""
    from . import gabarito

    casos = gabarito.ler_casos(planilha or gabarito.PLANILHA)
    if not gabarito.RESPOSTAS.exists():
        typer.echo("Ainda não há respostas da IA. Rode antes: python -m radar gabarito-rodar", err=True)
        raise typer.Exit(1)
    resultado = gabarito.conferir(casos, gabarito.ler_respostas())
    if not resultado["marcados"]:
        typer.echo("Nenhum caso marcado na planilha ainda: preencha as colunas amarelas da aba Casos.")
        return
    for dificuldade, (certos, n) in resultado["por_dificuldade"].items():
        if n:
            typer.echo(f"  {dificuldade}: {certos} de {n}")
    typer.echo(f"Acerto: {resultado['certos']} de {resultado['marcados']} casos marcados.")
    for nome, (certos, n) in resultado["secundario"].items():
        if n:
            typer.echo(f"  {nome}: {certos} de {n}")
    typer.echo(f"Resultado caso a caso em {gabarito.salvar_resultado(resultado)}")


@app.command("relatorio-mencoes")
def relatorio_mencoes(desde: str = typer.Option(None, help="Só posts publicados a partir de AAAA-MM-DD."),
                      saida: Path = typer.Option(None, help="Caminho do .xlsx.")):
    """Fase 2: planilha das menções com engajamento, sentimento, tema, alcance e repercussão."""
    caminho, n = relatorio.exportar(db.conectar(), saida, desde)
    typer.echo(f"{n} menções exportadas para {caminho}")


@app.command("exportar-app")
def exportar_app(saida: Path = typer.Option(None, help="Caminho do JSON (padrão: mobile/src/dados/radar.json).")):
    """Gera o JSON lido pelo app (pasta mobile/): menções, contas monitoradas e o mapa dos municípios."""
    caminho, n = app_dados.exportar(db.conectar(), saida)
    typer.echo(f"{n['mencoes']} menções e {n['contas']} contas exportadas para {caminho}")


@app.command()
def painel(porta: int = typer.Option(8787, help="Porta do painel no navegador."),
           rede: bool = typer.Option(False, "--rede", help="Abre também para outros aparelhos da mesma rede Wi-Fi "
                                                           "(o celular, por exemplo)."),
           sem_abrir: bool = typer.Option(False, "--sem-abrir", help="Não abre o navegador sozinho.")):
    """Painel no navegador, no estilo dos serviços de streaming, com o que a vigilância coleta, ao vivo.

    Lê o banco só para leitura, então pode ficar aberto junto com a vigilância. Enquanto roda, baixa as capas dos
    posts e as fotos dos perfis para data/capas/ (os endereços do Instagram vencem em ~4 dias)."""
    from . import painel as painel_mod

    painel_mod.servir(porta, rede=rede, abrir=not sem_abrir, aviso=typer.echo)


@app.command()
def capas(limite: int = typer.Option(2000, help="Máximo de imagens nesta rodada.")):
    """Baixa agora as capas e fotos pendentes (o painel faz isso sozinho enquanto está aberto). Grátis: vêm direto da
    rede de distribuição do Instagram, sem a conta logada."""
    from . import capas as capas_mod

    con = db.conectar()
    feitos = capas_mod.rodada(con, limite=limite)
    apagadas = capas_mod.limpar(con)
    typer.echo(f"{feitos['baixadas']} imagens baixadas, {feitos['falhas']} falharam (endereço vencido ou sem rede), "
               f"{feitos['faltam']} ficaram para a próxima; {apagadas} capas de posts fora da Energisa apagadas. "
               f"Pasta: {capas_mod.PASTA}")


@app.command()
def site(saida: Path = typer.Option(Path("site"), help="Pasta do site (padrão: site/).")):
    """Grava o painel completo sem servidor, para o GitHub Pages (https://lpkramer.github.io/RadarRep/): a resposta
    da API a cada pedido das telas, com as capas, as fotos e a mídia dos stories já baixadas. Ao publicar, tudo o que
    o painel mostra fica público. Grátis: só lê o banco e a pasta de dados."""
    from . import estatico

    feito = estatico.exportar(db.conectar_leitura(), saida)
    typer.echo(f"Site em {saida.resolve()}: {feito['mencoes']} menções, {feito['contas']} contas, "
               f"{len(feito['com_capa'])} capas, {len(feito['com_foto'])} fotos e {len(feito['midias'])} mídias de "
               f"stories; {feito['arquivos']} arquivos de dados ({feito['bytes'] / 1e6:.1f} MB).")


@app.command()
def imagens(sim: bool = typer.Option(False, "--sim", help="Não pede confirmação do custo."),
            max_usd: float = typer.Option(1.5, "--max-usd", help="Teto de gasto na Apify; acima dele, nem começa.")):
    """Busca pela Apify as fotos das contas que falaram da Energisa e as capas das menções que faltam no painel, e
    já as baixa para data/capas/. Paga por resultado (~US$ 2,60 por mil perfis); só lê o que falta."""
    from . import imagens as imagens_mod

    con = db.conectar()
    contas = imagens_mod.contas_sem_foto(con)
    mencoes_ = imagens_mod.mencoes_sem_capa(con)
    custo = imagens_mod.custo(len(contas), len(mencoes_))
    typer.echo(f"{len(contas)} contas sem foto e {len(mencoes_)} menções sem capa.")
    if not contas and not mencoes_:
        return
    if custo > max_usd:
        typer.echo(f"Custo estimado de US$ {custo:.2f} passa do teto (--max-usd {max_usd:.2f}).", err=True)
        raise typer.Exit(1)
    _confirmar(custo, sim)
    apify = _apify()
    resumo = imagens_mod.buscar(con, apify, contas, mencoes_, typer.echo)
    imagens_mod.baixar_agora(con, mencoes_, typer.echo)
    typer.echo(f"{resumo['perfis']} perfis lidos e {resumo['posts']} menções relidas pelo link. "
               f"Gasto na Apify: US$ {apify.gasto_usd:.2f}.")


@app.command("importar-brutos")
def importar_brutos():
    """Reprocessa os JSON salvos em data/raw/ sem chamar a Apify (útil se o banco for recriado)."""
    con = db.conectar()
    municipios = mun.carregar(con)
    por_termo = {mun.normalizar(t.termo): t for t in tm.gerar(municipios)}
    por_consulta = {c.termo: c for c in tm.consultas_google(municipios) + tm.consultas_google_estado(municipios)}
    n = 0
    for bruto in apify_mod.ler_brutos():
        itens, rotulo = bruto["itens"], bruto["rotulo"]
        if rotulo == "busca":
            contagem = descoberta.ingerir_busca(con, itens, por_termo, municipios)
            for termo in (t.strip() for t in bruto["entrada"]["search"].split(",")):
                t = por_termo.get(mun.normalizar(termo))
                db.registrar_busca(con, "busca", termo, t.ibge_id if t else None, bruto["run_id"],
                                   contagem.get(termo, 0))
        elif rotulo in ("perfil", "monitor"):
            for item in itens:
                # Mantém a profundidade já conhecida (o padrão 0 transformaria a conta em semente de expansão).
                atual = con.execute("SELECT profundidade FROM contas WHERE rede = 'instagram' AND username = ?",
                                    ((item.get("username") or "").lower(),)).fetchone()
                perfis.ingerir(con, item, "relacionada", municipios, atual[0] if atual else 0)
        elif rotulo == "comentarios":
            monitoramento.ingerir_comentarios(con, itens)
        elif rotulo == "monitor_posts":
            for dono, posts in monitoramento.agrupar_por_dono(itens).items():
                perfis.ingerir_posts(con, dono, posts)
        elif rotulo == "historico":
            entrada = bruto["entrada"]
            monitoramento.ingerir_historico(con, itens, entrada["username"], entrada["onlyPostsNewerThan"],
                                            entrada.get("resultsLimit"))
        elif rotulo == "hashtag":
            descoberta.ingerir_hashtags(con, itens)
        elif rotulo == "google":
            descoberta.ingerir_google(con, itens, por_consulta)
        con.commit()
        n += 1
    typer.echo(f"{n} arquivos reprocessados. Pré-filtro: {dict(enriquecimento.prefiltrar(con))}")


influentes_app = typer.Typer(help="As 1.000 páginas e perfis do TO com mais seguidores (sem empresas e órgãos "
                                  "públicos), que completam a base monitorada. A descoberta é feita pelo navegador.",
                             no_args_is_help=True)
app.add_typer(influentes_app, name="influentes")


@influentes_app.command("descobrir")
def influentes_descobrir(orcamento: int = typer.Option(800, help="Leituras pelo navegador nesta rodada."),
                         piso: int = typer.Option(influentes.CORTE_INICIAL,
                                                  help="Seguidores mínimos enquanto a lista ainda não tem 1.000."),
                         tamanho: int = typer.Option(influentes.TAMANHO_LISTA,
                                                     help="Meta: o corte é a conta nessa posição (ex.: 100)."),
                         foco: str = typer.Option("paginas", help="paginas ou influenciadores (só pessoas criadoras "
                                                                  "de conteúdo; sementes e fila vêm deles)."),
                         dry_run: bool = typer.Option(False, "--dry-run", help="Só mostra a situação, sem leituras."),
                         ver: bool = typer.Option(False, "--ver", help="Mostra a janela do navegador.")):
    """Uma rodada da descoberta: busca, contas parecidas e perfis. Retoma de onde parou; rode em várias noites."""
    con = db.conectar()
    if dry_run:
        typer.echo(str(influentes.situacao(con, piso)))
        return
    typer.echo(f"Rodada de até {_tempo_navegador(orcamento)}.")
    with _coletor("navegador", con, ver or config.NAV_VISIVEL) as coletor:
        resumo = influentes.rodada(con, coletor, orcamento, piso, typer.echo, tamanho, foco)
    typer.echo(str(resumo))
    typer.echo("Quando a fila acabar ou o corte parar de subir: python -m radar classificar e, depois, "
               "python -m radar influentes lista.")


@influentes_app.command("ranking")
def influentes_ranking(tamanho: int = typer.Option(100, help="Quantas contas."),
                       foco: str = typer.Option("paginas", help="paginas ou influenciadores."),
                       sem_orgaos: bool = typer.Option(False, "--sem-orgaos", help="Tira prefeituras e governo."),
                       saida: Path = typer.Option(None, help="Caminho do .xlsx.")):
    """As contas do TO com mais seguidores, sem empresas, numa planilha (não muda o monitoramento)."""
    import pandas as pd

    con = db.conectar()
    if foco == "influenciadores":
        linhas = influentes.ranking_influenciadores(con, tamanho)
        for linha in linhas:
            linha["tipo"] = "influenciador" if linha["confirmada"] else None
    else:
        linhas = influentes.ranking(con, tamanho, not sem_orgaos)
    caminho = Path(saida or config.EXPORT_DIR / f"mais_seguidas_to_{foco}_{tamanho}.xlsx")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tabela = pd.DataFrame([{
        "posicao": i, "conta": f"@{l['username']}", "nome": l["nome"], "seguidores": l["seguidores"],
        "tipo": l["tipo"] or "a confirmar (sem classificação)", "categoria": l["categoria"] or "",
        "municipios": l["municipios_ia"] or "", "bio": (l["bio"] or "").replace("\n", " ")[:200],
        "verificada": "sim" if l["verificada"] else "", "link": f"https://www.instagram.com/{l['username']}/",
    } for i, l in enumerate(linhas, 1)])
    tabela.to_excel(caminho, index=False)
    a_confirmar = sum(1 for l in linhas if not l["tipo"])
    typer.echo(f"{len(linhas)} contas em {caminho}; {a_confirmar} ainda sem classificação da IA.")
    for i, linha in enumerate(linhas[:20], 1):
        typer.echo(f"  {i:>3}. @{linha['username']:28} {linha['seguidores'] or 0:>10,}  {linha['tipo'] or 'a confirmar'}"
                   .replace(",", "."))


@influentes_app.command("classificar")
def influentes_classificar(tamanho: int = typer.Option(300, help="Até quantos candidatos do ranking."),
                           dry_run: bool = typer.Option(False, "--dry-run")):
    """IA (DeepSeek) só nos candidatos a influenciador do ranking que ainda não passaram pelo prompt novo."""
    con = db.conectar()
    contas = influentes.a_confirmar(con, tamanho)
    typer.echo(f"{len(contas)} candidatos a influenciador para a IA confirmar.")
    _classificar_deepseek(con, [dict(con.execute("SELECT * FROM contas WHERE rede = 'instagram' AND username = ?",
                                                 (c["username"],)).fetchone()) for c in contas], dry_run)


@influentes_app.command("lista")
def influentes_lista(tamanho: int = typer.Option(None, help="Contas na lista (padrão: 1000 páginas, 100 "
                                                              "influenciadores)."),
                     foco: str = typer.Option("paginas", help="paginas (as mais seguidas) ou influenciadores.")):
    """Grava as páginas e perfis do TO com mais seguidores (ou os influenciadores). Eles passam a ser monitorados
    como os outros; os influenciadores, a cada 1 hora na vigilância."""
    if foco == "influenciadores":
        tamanho = tamanho or influentes.TAMANHO_INFLUENCIADORES
        linhas = influentes.montar_lista_influenciadores(db.conectar(), tamanho)
        confirmados = sum(l["confirmada"] for l in linhas)
        typer.echo(f"{len(linhas)} influenciadores na lista ({confirmados} confirmados pela IA; o resto pela regra "
                   "até o: python -m radar influentes classificar). Entram no monitorar, vigiar e mencoes.")
        for i, linha in enumerate(linhas[:15], 1):
            typer.echo(f"  {i:>3}. @{linha['username']:30} {linha['seguidores'] or 0:>10,}".replace(",", "."))
        return
    tamanho = tamanho or influentes.TAMANHO_LISTA
    linhas = influentes.montar_lista(db.conectar(), tamanho)
    typer.echo(f"{len(linhas)} contas na lista das mais seguidas do TO; entram no monitorar, historico e mencoes.")
    for i, linha in enumerate(linhas[:15], 1):
        typer.echo(f"  {i:>3}. @{linha['username']:30} {linha['seguidores'] or 0:>10,}  {linha['tipo']}"
                   .replace(",", "."))
    if len(linhas) < tamanho:
        typer.echo(f"Faltam {tamanho - len(linhas)}: rode mais rodadas do influentes descobrir (ou baixe o --piso) "
                   "e o classificar.")


@app.command("buscar-fofoca")
def buscar_fofoca(orcamento: int = typer.Option(3000, help="Leituras pelo navegador nesta execução."),
                  conta: str = typer.Option("principal", help="Conta do navegador (uma que não esteja na vigilância)."),
                  sem_ia: bool = typer.Option(False, "--sem-ia", help="Só busca e lê; a IA fica para depois."),
                  dry_run: bool = typer.Option(False, "--dry-run", help="Só mostra a situação, sem leituras."),
                  ver: bool = typer.Option(False, "--ver", help="Mostra a janela do navegador.")):
    """Páginas de fofoca em todos os municípios do TO, pela busca do Instagram, com a IA classificando as achadas.
    As de fofoca do TO entram na vigilância. No teto diário da conta, espera e continua; retoma de onde parou."""
    import time

    from . import fofoca, navegador

    con = db.conectar()
    antes = fofoca.situacao(con)
    typer.echo(f"Antes: {antes}")
    if dry_run:
        return

    def classificar(contas):
        try:
            _classificar_deepseek(con, contas, False)
            typer.echo(f"Prioridades: {sc.pontuar(con)}")
        except Exception as e:  # sem rede ou sem saldo: a busca segue, e as contas ficam para a próxima vez
            typer.echo(f"IA não rodou agora ({type(e).__name__}: {e}); as contas ficam para depois.", err=True)

    try:
        with navegador.abrir(ver or config.NAV_VISIVEL, conta=conta) as coletor:
            while True:
                try:
                    resumo = fofoca.rodada(con, coletor, orcamento, typer.echo, None if sem_ia else classificar)
                    break
                except navegador.TetoAtingido as e:
                    typer.echo(f"Teto de leituras da conta {conta}; espero {e.espera / 3600:.1f} h e continuo.")
                    if not sem_ia and fofoca.a_classificar(con):
                        classificar(fofoca.a_classificar(con))
                    time.sleep(e.espera + 60)
    except (navegador.ColetaInterrompida, navegador.SemLogin) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)
    typer.echo(f"Resumo: {resumo}")
    depois = fofoca.cobertura(con)
    typer.echo(f"Páginas de fofoca do TO: {antes['paginas_de_fofoca']} -> {depois['paginas_de_fofoca']}; "
               f"municípios com página de fofoca: {antes['municipios_com_fofoca']} -> "
               f"{depois['municipios_com_fofoca']} de 139.")


def _investigar_autores(con, coletor) -> None:
    """A DeepSeek situa quem postou sem dizer de onde é. Com o `coletor`, lê o perfil dos que ficarem em dúvida."""
    from . import classificacao_deepseek as ds, investigacao

    autores = investigacao.pendentes(con)
    if not autores:
        return
    _exigir_deepseek()
    typer.echo(f"Investigando {len(autores)} contas (onde mora quem postou)...")
    try:
        resumo = investigacao.investigar(con, ds.criar_cliente(), coletor, autores, aviso=typer.echo)
    except RuntimeError as e:  # sem chave ou sem saldo
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(f"Investigação: {dict(resumo)}")


@app.command("buscar-posts")
def buscar_posts_(conta: str = typer.Option("principal", help="Conta do navegador (uma que não esteja na "
                                                                 "vigilância)."),
                  dias: int = typer.Option(30, help="Só posts dos últimos N dias."),
                  sem_ia: bool = typer.Option(False, "--sem-ia", help="Só busca e grava; a IA fica para depois."),
                  so_ia: bool = typer.Option(False, "--so-ia", help="Não busca: só investiga os autores pendentes (sem "
                                                                   "ler perfis) e analisa os achados."),
                  reprocessar: bool = typer.Option(False, "--reprocessar", help="Não busca: passa os posts das "
                                                   "buscas guardados em data/raw/ pela triagem de novo."),
                  dry_run: bool = typer.Option(False, "--dry-run", help="Só mostra a situação, sem leituras."),
                  ver: bool = typer.Option(False, "--ver", help="Mostra a janela do navegador.")):
    """Posts de qualquer conta com as palavras-chave (a marca, temas de energia e o jeito de falar das pessoas),
    pela busca por palavra-chave do Instagram: uma leitura por termo. Quem posta sem dizer de onde é passa pela
    investigação: a DeepSeek determina a localização provável da conta, lendo o perfil quando fica em dúvida. Os
    posts do TO vão para a IA e a menção negativa vira alerta. A vigilância já roda isso a cada 6 h."""
    from . import busca_posts, navegador

    con = db.conectar()
    typer.echo(f"Antes: {busca_posts.situacao(con)}")
    if dry_run:
        return
    if reprocessar:
        typer.echo(f"Brutos: {dict(busca_posts.reprocessar_brutos(con, dias))}")
    elif not so_ia:
        total = Counter()
        try:
            with navegador.abrir(ver or config.NAV_VISIVEL, conta=conta) as coletor:
                for termo in busca_posts.TERMOS:
                    resumo = busca_posts.buscar(con, coletor, termo, dias=dias)
                    total.update(resumo)
                    typer.echo(f"{termo}: {dict(resumo)}")
                typer.echo(f"Busca: {dict(total)}")
                if not sem_ia:
                    _investigar_autores(con, coletor)
        except (navegador.ColetaInterrompida, navegador.SemLogin) as e:
            typer.echo(str(e), err=True)
    if not sem_ia:
        _investigar_autores(con, None)  # os que sobraram, só com a etapa sem leitura
    posts = busca_posts.candidatos(con)
    typer.echo(f"{len(posts)} posts achados para a IA analisar. "
               f"Custo estimado na DeepSeek: até US$ {mencoes.custo(len(posts)):.2f}")
    if posts and not sem_ia:
        _exigir_deepseek()
        _com_deepseek(lambda cliente: mencoes.analisar(con, cliente, posts, aviso=typer.echo))
        from .vigia import alertar

        typer.echo(f"{alertar(con, posts, typer.echo)} alertas de menção negativa em data/alertas.txt.")
        _consolidar_fatos(con)
    typer.echo(f"Depois: {busca_posts.situacao(con)}")


@app.command()
def status():
    """Resumo da base e do gasto acumulado na Apify."""
    con = db.conectar()

    def contar(sql):
        return dict(con.execute(sql).fetchall())

    typer.echo(f"Municípios: {con.execute('SELECT COUNT(*) FROM municipios').fetchone()[0]}")
    typer.echo(f"Buscas executadas: {contar('SELECT fonte, COUNT(*) FROM buscas GROUP BY fonte')}")
    typer.echo(f"Contas por origem: {contar('SELECT origem, COUNT(*) FROM contas GROUP BY origem')}")
    typer.echo(f"Descartadas: {contar('SELECT descartada, COUNT(*) FROM contas WHERE descartada IS NOT NULL GROUP BY 1')}")
    typer.echo(f"Classificadas por tipo: {contar('SELECT tipo, COUNT(*) FROM classificacoes GROUP BY tipo')}")
    typer.echo(f"Prioridade: {contar('SELECT prioridade, COUNT(*) FROM score GROUP BY prioridade')}")
    typer.echo(f"Revisão: {contar('SELECT status, COUNT(*) FROM revisao GROUP BY status')}")
    typer.echo(f"Posts guardados: {con.execute('SELECT COUNT(*) FROM posts').fetchone()[0]}; "
               f"monitoradas: {len(monitoramento.monitoradas(con))}, das quais "
               f"{con.execute('SELECT COUNT(*) FROM listas WHERE lista = ?', (influentes.LISTA,)).fetchone()[0]} "
               "entre as mais seguidas do TO")
    typer.echo(f"Menções sobre energia por sentimento: "
               f"{contar('SELECT sentimento, COUNT(*) FROM mencoes WHERE sobre_energia = 1 GROUP BY 1')}")
    gasto = sum(float(b.get("usd") or 0) for b in apify_mod.ler_brutos())
    typer.echo(f"Gasto registrado na Apify: US$ {gasto:.2f}")


@app.command()
def pipeline(municipio: str = typer.Option(None, help="Roda só para um município (piloto)."),
             direto: bool = typer.Option(False, help="Classifica sem batch."),
             max_perfis: int = typer.Option(1500, help="Teto de perfis coletados por rodada de expansão."),
             sim: bool = typer.Option(False, "--sim", "-y")):
    """Roda descobrir → classificar → expandir (até a profundidade máxima) → exportar."""
    descobrir(municipio=municipio, dry_run=False, hashtags=False, google=False,
              search_limit=config.SEARCH_LIMIT, por_hashtag=100, busca=True, cobertura_max=None, sim=sim)
    classificar(direto=direto, limite=None, reclassificar=False, nao_esperar=False, dry_run=False)
    for passo in range(1, config.PROFUNDIDADE_MAX + 1):
        typer.echo(f"--- Expansão {passo} ---")
        if descoberta.expandir(db.conectar()) == 0:
            break
        enriquecer(limite=max_perfis, dry_run=False, sim=sim)
        classificar(direto=direto, limite=None, reclassificar=False, nao_esperar=False, dry_run=False)
    exportar(saida=None)


if __name__ == "__main__":
    app()
