import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from radar import config, db, navegador as nav, perfis, vigia

from conftest import ApifyFalso, analise, conta, deepseek_falso, perfil, post, resposta_deepseek


def preparar(con, municipios):
    conta(con, municipios, "portalgrande", [], seguidores=50_000)              # portal grande: alta
    conta(con, municipios, "portalpequeno", [], seguidores=5_000)              # portal pequeno, sem menção: média
    conta(con, municipios, "deputado", [], tipo="politico", seguidores=90_000)  # político: baixa


def test_intervalo_por_prioridade(con, municipios):
    preparar(con, municipios)
    horas = vigia.intervalos(con)
    assert horas == {"portalgrande": config.VIGIA_HORAS["alta"], "portalpequeno": config.VIGIA_HORAS["media"],
                     "deputado": config.VIGIA_HORAS["baixa"]}
    # Uma menção sobre energia põe o portal pequeno na prioridade alta.
    perfis.ingerir_posts(con, "portalpequeno", [post(1, legenda="Falta de energia", codigo="M1")])
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, tema, sentimento, alcance, "
                "resumo, confianca, modelo, analisada_em) VALUES ('instagram', 'id-M1', 1, 0, 'falta_energia', "
                "'negativo', 'local', 'x', 0.9, 'm', 'x')")
    assert vigia.intervalos(con)["portalpequeno"] == config.VIGIA_HORAS["alta"]


def test_dois_trabalhadores_nunca_pegam_a_mesma_pagina(con, municipios, tmp_path):
    caminho = tmp_path / "radar.db"
    a, b = db.conectar(caminho), db.conectar(caminho)
    for username in ("p1", "p2", "p3"):
        perfis.ingerir(a, perfil(username), "busca", [])
    horas = {"p1": 2, "p2": 2, "p3": 2}
    agora = datetime.now(timezone.utc)
    pegas = [vigia.reservar(a, "conta1", agora, horas), vigia.reservar(b, "conta2", agora, horas),
             vigia.reservar(a, "conta1", agora, horas), vigia.reservar(b, "conta2", agora, horas)]
    assert sorted(pegas[:3]) == ["p1", "p2", "p3"] and pegas[3] is None
    vigia.liberar(a, "p2")  # liberada e ainda sem visita: volta a estar vencida
    assert vigia.reservar(b, "conta2", agora, horas) == "p2"


def test_vigiar_visita_as_atrasadas_e_alerta_menção_negativa(con, municipios, tmp_path, monkeypatch):
    monkeypatch.setattr(vigia, "ARQUIVO_ALERTAS", tmp_path / "alertas.txt")
    preparar(con, municipios)
    novo = [post(0, legenda="Falta de energia no centro há 6 horas", codigo="N1") | {"ownerUsername": "portalgrande"}]
    coletor = ApifyFalso({config.ATOR_POSTS: novo})
    ia, _ = deepseek_falso(lambda _: resposta_deepseek(analise(sentimento="negativo")))
    relogio = [datetime.now(timezone.utc)]

    def dormir(segundos):
        relogio[0] += timedelta(seconds=segundos)

    resumo = vigia.vigiar(con, coletor, "principal", relogio[0] + timedelta(hours=3), aviso=lambda _: None, ia=ia,
                          relogio=lambda: relogio[0], dormir=dormir)
    visitadas = [entrada["username"][0] for _, entrada in coletor.chamadas]
    # Em 3 horas: as três na primeira volta e, depois de 2 horas, o portal grande de novo.
    assert sorted(visitadas[:3]) == ["deputado", "portalgrande", "portalpequeno"]
    assert visitadas[3:] == ["portalgrande"]
    assert resumo["mencoes"] == 1
    alertas = (tmp_path / "alertas.txt").read_text(encoding="utf-8").splitlines()
    assert len(alertas) == 1 and "@portalgrande" in alertas[0]  # a segunda visita não repete o alerta
    assert con.execute("SELECT COUNT(*) FROM reservas").fetchone()[0] == 0


def test_volta_passa_uma_vez_pelas_paginas_sem_visita_recente_e_termina(con, municipios, monkeypatch):
    monkeypatch.setattr(config, "VIGIA_VOLTA_H", 4)
    monkeypatch.setattr(config, "VIGIA_PRAZO_STORIES_H", 20)
    preparar(con, municipios)
    conta(con, municipios, "fofocato", [], prioridade="C", tipo="perfil_fofoca")
    inicio = datetime.now(timezone.utc)
    visitas = {"portalgrande": 1, "portalpequeno": 5, "deputado": 30, "fofocato": 6}  # horas desde a última visita
    con.executemany("INSERT INTO visitas (rede, username, ultima_em) VALUES ('instagram', ?, ?)",
                    [(u, (inicio - timedelta(hours=h)).isoformat(timespec="seconds")) for u, h in visitas.items()])
    con.commit()
    horas = vigia.intervalos(con)
    # O portal grande, visitado há 1 h, fica de fora da volta. O deputado passou do prazo dos stories e vai primeiro;
    # depois, a prioridade: a fofoca (intervalo de 1 h) antes do portal pequeno (6 h).
    assert [u for _, u in vigia.atrasadas(con, inicio, horas, volta=True)] == ["deputado", "fofocato", "portalpequeno"]
    coletor = ApifyFalso({config.ATOR_POSTS: []})
    relogio = [inicio]

    def dormir(segundos):
        relogio[0] += timedelta(seconds=segundos)

    avisos = []
    resumo = vigia.vigiar(con, coletor, "principal", inicio + timedelta(hours=6), aviso=avisos.append,
                          relogio=lambda: relogio[0], dormir=dormir, volta=True)
    visitadas = [e["username"][0] for a, e in coletor.chamadas if a == config.ATOR_POSTS]
    assert visitadas == ["deputado", "fofocato", "portalpequeno"] and resumo["contas"] == 3
    assert relogio[0] == inicio  # terminou sem esperar o limite de 6 h
    assert "sem página" in avisos[-1]
    assert vigia.atrasadas(con, inicio, horas, volta=True) == []  # uma volta repetida logo depois não relê nada
    assert con.execute("SELECT COUNT(*) FROM reservas").fetchone()[0] == 0


def test_volta_mais_longa_que_a_janela_nao_revisita_e_termina(con, municipios, monkeypatch):
    monkeypatch.setattr(config, "VIGIA_VOLTA_H", 4)
    preparar(con, municipios)
    inicio = datetime.now(timezone.utc)
    visitas = {"portalgrande": 1, "portalpequeno": 5, "deputado": 30}  # horas desde a última visita
    con.executemany("INSERT INTO visitas (rede, username, ultima_em) VALUES ('instagram', ?, ?)",
                    [(u, (inicio - timedelta(hours=h)).isoformat(timespec="seconds")) for u, h in visitas.items()])
    con.commit()
    coletor = ApifyFalso({config.ATOR_POSTS: []})
    relogio = [inicio]

    def passam_duas_horas():  # cada consulta ao relógio leva 2 h: a volta dura mais que as 4 h da janela
        relogio[0] += timedelta(hours=2)
        return relogio[0]

    avisos = []
    resumo = vigia.vigiar(con, coletor, "principal", inicio + timedelta(hours=100), aviso=avisos.append,
                          relogio=passam_duas_horas, dormir=lambda _: None, volta=True)
    visitadas = [e["username"][0] for a, e in coletor.chamadas if a == config.ATOR_POSTS]
    # Nem a página visitada pela própria volta nem a visitada 1 h antes do começo voltam à fila, por mais que passe
    # o tempo.
    assert visitadas == ["deputado", "portalpequeno"] and resumo["contas"] == 2
    assert relogio[0] - inicio > timedelta(hours=4) and "sem página" in avisos[-1]


def test_so_a_volta_abre_o_navegador_no_ritmo_fixo(monkeypatch, tmp_path):
    monkeypatch.setattr(vigia, "ARQUIVO_PARAR", tmp_path / "PARAR")
    monkeypatch.setattr(config, "VIGIA_VOLTA_POR_HORA", 60)
    pedidos = []

    @contextmanager
    def abrir(visivel, conta, dormir, uniforme, por_hora=None):
        pedidos.append((conta, uniforme, por_hora))
        yield object()

    monkeypatch.setattr(nav, "abrir", abrir)
    monkeypatch.setattr(vigia, "vigiar", lambda *args, **kwargs: {"contas": 0})
    ate = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    banco = str(tmp_path / "radar.db")
    assert vigia.trabalhador("principal", ate, banco, False, False, volta=True) == {"conta": "principal", "contas": 0}
    assert vigia.trabalhador("conta2", ate, banco, False, False) == {"conta": "conta2", "contas": 0}
    # A vigilância contínua segue espalhando o teto do dia.
    assert pedidos == [("principal", True, 60), ("conta2", True, None)]


def test_volta_repassa_ao_trabalhador_e_nao_espera_contas_novas(monkeypatch, tmp_path):
    monkeypatch.setattr(vigia, "ARQUIVO_PARAR", tmp_path / "PARAR")
    recebidos, procuras = [], []

    def trabalhar(conta, ate, banco, visivel, com_ia, atraso, volta=False):
        recebidos.append((conta, volta))
        return {"conta": conta, "contas": 10}

    def procurar(conhecidas):
        procuras.append(conhecidas)
        return []

    resultados = vigia.executar(["principal", "conta2"], 6, aviso=lambda _: None, procurar_novas=procurar,
                                trabalhar=trabalhar, executor_cls=ThreadPoolExecutor,
                                esperar_contas=lambda _: None, volta=True)
    assert sorted(recebidos) == [("conta2", True), ("principal", True)] and procuras == []
    assert [r["contas"] for r in resultados] == [10, 10]  # terminaram sem nova tentativa


def test_sem_rede_a_ia_fica_para_depois(con, municipios, monkeypatch):
    preparar(con, municipios)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    assert vigia.ia_disponivel() is False


def test_influenciadores_e_fofoca_do_to_a_cada_hora(con, municipios):
    preparar(con, municipios)
    conta(con, municipios, "fofocato", [], prioridade="C", tipo="perfil_fofoca")  # score baixo: entra mesmo assim
    conta(con, municipios, "fofocasp", [], prioridade="C", tipo="perfil_fofoca", municipios=[],
          abrangencia="nacional")  # fofoca de fora do TO: não entra
    conta(con, municipios, "influ", [], prioridade="C", tipo="influenciador_local")
    con.execute("INSERT INTO listas (rede, username, lista, posicao, seguidores, atualizada_em) "
                "VALUES ('instagram', 'influ', 'influenciadores_to', 1, 10000, 'x')")
    horas = vigia.intervalos(con)
    assert horas["fofocato"] == horas["influ"] == config.VIGIA_HORAS["foco"] == 1
    assert "fofocasp" not in horas and horas["portalgrande"] == config.VIGIA_HORAS["alta"]


class ColetorComTeto(ApifyFalso):
    """Estoura o teto diário na primeira leitura e pede 1 hora de espera."""

    def __init__(self, respostas):
        super().__init__(respostas)
        self.estourou, self.espera = False, 0

    def rodar(self, ator, entrada, rotulo, max_usd=None):
        if not self.estourou:
            self.estourou, self.espera = True, 3600
            raise nav.TetoAtingido("Teto", 3600)
        return super().rodar(ator, entrada, rotulo, max_usd)

    def espera_do_teto(self):
        espera, self.espera = self.espera, 0
        return espera


def test_no_teto_diario_a_vigilancia_espera_e_continua(con, municipios):
    preparar(con, municipios)
    coletor = ColetorComTeto({config.ATOR_POSTS: []})
    relogio = [datetime.now(timezone.utc)]
    inicio = relogio[0]

    def dormir(segundos):
        relogio[0] += timedelta(seconds=segundos)

    resumo = vigia.vigiar(con, coletor, "principal", inicio + timedelta(hours=1, minutes=30), aviso=lambda _: None,
                          relogio=lambda: relogio[0], dormir=dormir)
    assert resumo["contas"] == 3  # não parou: esperou a hora e visitou as três
    primeira_visita = perfis.ler_data(con.execute("SELECT MIN(ultima_em) FROM visitas").fetchone()[0])
    assert primeira_visita - inicio >= timedelta(hours=1)
    assert con.execute("SELECT COUNT(*) FROM reservas").fetchone()[0] == 0


def test_pedido_de_parada_encerra_o_trabalhador(con, municipios):
    preparar(con, municipios)
    coletor = ApifyFalso({config.ATOR_POSTS: []})
    resumo = vigia.vigiar(con, coletor, "principal", datetime.now(timezone.utc) + timedelta(hours=3),
                          aviso=lambda _: None, parar=lambda: len(coletor.chamadas) >= 1)
    assert resumo["contas"] == 1
    assert con.execute("SELECT COUNT(*) FROM reservas").fetchone()[0] == 0


def test_cochilo_acorda_com_o_pedido_de_parada():
    dormidos = []
    with pytest.raises(vigia.Parada):
        vigia.cochilar(600, parar=lambda: len(dormidos) >= 2, dormir=dormidos.append)
    assert dormidos == [60, 60]


def test_duas_contas_barradas_em_uma_hora_param_todas(monkeypatch, tmp_path):
    monkeypatch.setattr(vigia, "ARQUIVO_PARAR", tmp_path / "PARAR")
    atrasos = {}

    def trabalhar(conta, ate, banco, visivel, com_ia, atraso):
        atrasos[conta] = atraso
        if conta in ("c1", "c2"):
            return {"conta": conta, "parou": "Instagram pediu para esperar", "bloqueio": True}
        limite = time.monotonic() + 5
        while not vigia.parada_pedida() and time.monotonic() < limite:
            time.sleep(0.01)
        return {"conta": conta, "parou": "a pedido"}

    resultados = vigia.executar(["c1", "c2", "c3"], 1, aviso=lambda _: None, trabalhar=trabalhar,
                                executor_cls=ThreadPoolExecutor)
    assert vigia.parada_pedida() and "barradas" in (tmp_path / "PARAR").read_text(encoding="utf-8")
    assert {r["conta"]: r["parou"] for r in resultados}["c3"] == "a pedido"
    # As contas entram escalonadas.
    assert atrasos["c1"] == 0 and atrasos["c2"] >= vigia.ESCALONAR and atrasos["c3"] >= 2 * vigia.ESCALONAR


def test_conta_logada_depois_entra_sozinha(monkeypatch, tmp_path):
    monkeypatch.setattr(vigia, "ARQUIVO_PARAR", tmp_path / "PARAR")
    entraram = []
    logadas = [[], ["nova"]]  # na primeira procura ainda não há conta nova; na segunda, há

    def procurar(conhecidas):
        novas = logadas.pop(0) if logadas else []
        if not novas and not logadas:
            vigia.pedir_parada("fim do teste")
        return [c for c in novas if c not in conhecidas]

    def trabalhar(conta, *_):
        entraram.append(conta)
        return {"conta": conta}

    vigia.executar(["principal"], 1, aviso=lambda _: None, procurar_novas=procurar, trabalhar=trabalhar,
                   executor_cls=ThreadPoolExecutor, esperar_contas=lambda _: None)
    assert entraram == ["principal", "nova"]


def test_falha_passageira_tenta_de_novo_e_bloqueio_nao(monkeypatch, tmp_path):
    monkeypatch.setattr(vigia, "ARQUIVO_PARAR", tmp_path / "PARAR")
    entradas = []

    def trabalhar(conta, ate, banco, visivel, com_ia, atraso):
        entradas.append((conta, atraso))
        if conta == "instavel" and len([c for c, _ in entradas if c == "instavel"]) == 1:
            return {"conta": conta, "parou": "TargetClosedError: o Edge fechou"}
        if conta == "barrada":
            return {"conta": conta, "parou": "429", "bloqueio": True}
        return {"conta": conta}

    vigia.executar(["instavel", "barrada"], 1, aviso=lambda _: None, trabalhar=trabalhar,
                   executor_cls=ThreadPoolExecutor)
    assert [c for c, _ in entradas].count("instavel") == 2 and [c for c, _ in entradas].count("barrada") == 1
    assert entradas[-1] == ("instavel", vigia.REINICIAR)


def test_processo_que_morre_ganha_processo_novo(monkeypatch, tmp_path):
    monkeypatch.setattr(vigia, "ARQUIVO_PARAR", tmp_path / "PARAR")
    executores, chamadas = [], []

    class Executor(ThreadPoolExecutor):
        def __init__(self, max_workers):
            super().__init__(max_workers)
            executores.append(self)

    def trabalhar(conta, *_):
        chamadas.append(conta)
        if len(chamadas) == 1:
            raise RuntimeError("A process in the process pool was terminated abruptly")
        return {"conta": conta}

    resultados = vigia.executar(["principal"], 1, aviso=lambda _: None, trabalhar=trabalhar, executor_cls=Executor)
    assert chamadas == ["principal", "principal"] and len(executores) == 2  # a nova tentativa usa outro executor
    assert "terminated abruptly" in resultados[0]["parou"] and "parou" not in resultados[1]


def test_stories_primeiro_toda_pagina_dentro_do_prazo(con, municipios, monkeypatch):
    monkeypatch.setattr(config, "VIGIA_PRAZO_STORIES_H", 20)
    agora = datetime.now(timezone.utc)
    horas = {"influ": 1, "portal": 2, "orgao": 24, "novo_orgao": 24, "novo_influ": 1, "orgao_em_dia": 24}
    visitas = {"influ": agora - timedelta(hours=3), "portal": agora - timedelta(hours=2, minutes=30),
               "orgao": agora - timedelta(hours=21), "orgao_em_dia": agora - timedelta(hours=19)}
    con.executemany("INSERT INTO visitas (rede, username, ultima_em) VALUES ('instagram', ?, ?)",
                    [(u, q.isoformat(timespec="seconds")) for u, q in visitas.items()])
    ordem = [u for _, u in vigia.atrasadas(con, agora, horas)]
    # Nunca visitadas (a de intervalo menor antes), depois a que passou do prazo dos stories, depois as revisitas.
    assert ordem == ["novo_influ", "novo_orgao", "orgao", "influ", "portal"]
    assert "orgao_em_dia" not in ordem  # 19 h: ainda dentro do prazo
    assert vigia.visitas_por_dia({"a": 24, "b": 1}) == 24 / 20 + 24


def test_visitas_sem_novidade_espacam_a_pagina(con, municipios, monkeypatch):
    monkeypatch.setattr(config, "VIGIA_PRAZO_STORIES_H", 20)
    assert [vigia.prazo(1, v) for v in (0, 1, 2, 3, 9)] == [1, 2, 4, 8, 8]
    assert vigia.prazo(6, 3) == 20  # nunca passa do prazo dos stories
    preparar(con, municipios)
    agora = datetime.now(timezone.utc)
    con.execute("INSERT INTO visitas (rede, username, ultima_em) VALUES ('instagram', 'portalgrande', ?)",
                ((agora - timedelta(hours=3)).isoformat(timespec="seconds"),))
    horas = {"portalgrande": 2}
    assert [u for _, u in vigia.atrasadas(con, agora, horas)] == ["portalgrande"]  # 3 h de 2 h: vencida
    vigia.marcar_visita(con, "portalgrande", novidade=False)
    assert vigia.atrasadas(con, agora, horas) == []  # sem novidade: agora o prazo é 4 h
    vigia.marcar_visita(con, "portalgrande", novidade=True)
    assert [u for _, u in vigia.atrasadas(con, agora, horas)] == ["portalgrande"]  # novidade: volta a 2 h


class ColetorLento(ApifyFalso):
    """Um post novo por conta e stories que demoram a ler (como a pausa do ritmo), para testar dois trabalhadores
    gravando no mesmo banco."""

    def __init__(self):
        super().__init__({})

    def rodar(self, ator, entrada, rotulo, max_usd=None):
        self.chamadas.append((ator, entrada))
        return "x", [post(0, codigo=f"P-{u}") | {"ownerUsername": u} for u in entrada.get("username", [])]

    def story_novo(self, username, ultimo):
        return ultimo is None

    def stories(self, username):
        time.sleep(0.3)  # a pausa antes da leitura: a gravação dos posts não pode ficar aberta aqui
        agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return [{"id": f"s-{username}-{i}", "timestamp": agora, "url": None, "texto": "", "imagem": None,
                 "video": None} for i in range(2)]

    def baixar(self, url, destino):
        return False


def test_dois_trabalhadores_em_paralelo_no_mesmo_banco(municipios, tmp_path, monkeypatch):
    monkeypatch.setattr(vigia, "RECALCULAR", timedelta(hours=1))
    caminho = tmp_path / "radar.db"
    base = db.conectar(caminho)
    from radar import municipios as mun
    mun.salvar(base, mun.ler_csv())
    todos = mun.carregar(base)
    paginas = [f"pagina{i}" for i in range(8)]
    for u in paginas:
        conta(base, todos, u, [])
    coletores, erros = {}, []

    def trabalhar(nome):
        con = db.conectar(caminho)
        con.execute("PRAGMA busy_timeout = 1000")  # falha rápido se o outro segurar a gravação
        coletores[nome] = ColetorLento()
        try:
            return vigia.vigiar(con, coletores[nome], nome, datetime.now(timezone.utc) + timedelta(seconds=6),
                                aviso=lambda _: None, dormir=lambda s: time.sleep(min(s, 0.05)))
        except Exception as erro:
            erros.append(erro)
            raise

    with ThreadPoolExecutor(2) as executor:
        resumos = list(executor.map(trabalhar, ["conta1", "conta2"]))
    assert not erros
    visitadas = [e["username"][0] for c in coletores.values() for a, e in c.chamadas if a == config.ATOR_POSTS]
    assert sorted(visitadas) == paginas  # todas, cada uma por um trabalhador só
    assert all(r["contas"] > 0 for r in resumos)  # os dois trabalharam
    assert base.execute("SELECT COUNT(*) FROM posts WHERE tipo = 'Story'").fetchone()[0] == 16
    assert base.execute("SELECT COUNT(*) FROM reservas").fetchone()[0] == 0
