import re
from datetime import datetime, timedelta, timezone

from radar import busca_posts as bp, config, investigacao as inv, mencoes, navegador as nav, vigia

from conftest import analise, deepseek_falso, perfil, resposta_deepseek

AGORA = datetime.now(timezone.utc)

# O que a DeepSeek "sabe" de cada conta: na etapa 1 (só a busca) e na etapa 2 (com o perfil lido).
VEREDITOS = {
    "mironbataguassu": ({"cidade": "Bataguassu", "uf": "MS", "confianca": 0.9, "evidencias": "cita Bataguassu"}, None),
    "luzimanguesnewto": ({"cidade": "Porto Nacional", "uf": "TO", "confianca": 0.9,
                          "evidencias": "Luzimangues é distrito de Porto Nacional"}, None),
    "pessoa_comum": ({"cidade": None, "uf": None, "confianca": 0.2, "evidencias": "sem pista"},
                     {"cidade": "Araguaína", "uf": "TO", "confianca": 0.8, "evidencias": "bio: Araguaína-TO"}),
}


def media(codigo, dono, legenda, nome="", dias=1):
    return {"pk": f"9{codigo}", "code": codigo, "media_type": 1, "caption": {"text": legenda},
            "taken_at": int((AGORA - timedelta(days=dias)).timestamp()), "like_count": 10, "comment_count": 2,
            "user": {"username": dono, "full_name": nome}}


def ia_falsa():
    """Responde à investigação (etapa 1 sem a bio, etapa 2 com ela) e à análise das menções."""
    def responder(params):
        sistema, usuario = params["messages"][0]["content"], params["messages"][1]["content"]
        if sistema != inv.INSTRUCOES:
            return resposta_deepseek(analise(municipios=["Porto Nacional"]))
        dono = re.search(r"Conta: @(\S+)", usuario).group(1)
        etapa1, etapa2 = VEREDITOS[dono]
        return resposta_deepseek(etapa2 if "Bio:" in usuario and etapa2 else etapa1)
    return deepseek_falso(responder)


class Coletor:
    def __init__(self, medias=()):
        self.medias = list(medias)
        self.perfis_lidos = []

    def buscar_posts(self, termo):
        return [nav.post_do_site(m) for m in self.medias]

    def rodar(self, ator, entrada, rotulo, max_usd=None):
        assert ator == config.ATOR_PERFIL
        self.perfis_lidos += entrada["usernames"]
        return "nav", [perfil(u, biography="Mãe, professora. Araguaína-TO") for u in entrada["usernames"]]


def _ingerir(con, municipios, medias):
    return bp.ingerir(con, "energisa", [nav.post_do_site(m) for m in medias], municipios, AGORA)


def test_deepseek_situa_quem_posta_sem_dizer_de_onde_e(con, municipios):
    _ingerir(con, municipios, [
        media("A1", "mironbataguassu", "Chegamos no limite do descaso da Energisa, sem energia"),
        media("A2", "luzimanguesnewto", "Dificuldade no atendimento! @energisa em LUZIMANGUES", "Luzimangues News"),
        media("A3", "pessoa_comum", "Até quando vamos aceitar esse abuso da ENERGISA? Sem energia de novo"),
    ])
    assert [a["username"] for a in inv.pendentes(con)] == ["mironbataguassu", "luzimanguesnewto", "pessoa_comum"]
    ia, chamadas = ia_falsa()
    coletor = Coletor()
    resumo = inv.investigar(con, ia, coletor, inv.pendentes(con), aviso=lambda _: None)
    assert resumo["etapa1"] == 2 and resumo["etapa2"] == 1 and resumo["do_to"] == 2 and resumo["de_fora"] == 1
    assert coletor.perfis_lidos == ["pessoa_comum"]  # só quem ficou em dúvida tem o perfil lido
    assert len(chamadas) == 4
    porto = next(m for m in municipios if m["nome"] == "Porto Nacional")["ibge_id"]
    linha = con.execute("SELECT cidade, uf, ibge_id, do_to, etapa FROM investigacoes WHERE username = "
                        "'luzimanguesnewto'").fetchone()
    assert tuple(linha) == ("Porto Nacional", "TO", porto, 1, 1)
    fora = con.execute("SELECT cidade, uf, do_to FROM investigacoes WHERE username = 'mironbataguassu'").fetchone()
    assert tuple(fora) == ("Bataguassu", "MS", 0)  # a localização de fora também fica guardada
    estados = dict(con.execute("SELECT p.shortcode, b.estado FROM posts_busca b JOIN posts p USING (rede, post_id)"))
    assert estados == {"A1": "descartado", "A2": "do_to", "A3": "do_to"}
    assert sorted(p["shortcode"] for p in bp.candidatos(con)) == ["A2", "A3"]
    assert inv.pendentes(con) == []

    # Um post novo das mesmas contas decide na hora, sem perguntar de novo.
    resumo = _ingerir(con, municipios, [media("B1", "mironbataguassu", "Energisa, cadê a luz?"),
                                        media("B2", "luzimanguesnewto", "Sem energia de novo")])
    assert resumo["conta_de_fora"] == 1 and resumo["do_to"] == 1 and resumo["a_investigar"] == 0


def test_no_teto_do_dia_fica_o_veredito_da_etapa_1(con, municipios, monkeypatch):
    _ingerir(con, municipios, [media("C1", "pessoa_comum", "Sem energia de novo, Energisa")])
    monkeypatch.setattr(config, "INVESTIGAR_PERFIS_DIA", 0)  # o teto do dia já chegou
    monkeypatch.setitem(VEREDITOS, "pessoa_comum", ({"cidade": "Palmas", "uf": "TO", "confianca": 0.6,
                                                     "evidencias": "talvez"}, None))
    coletor = Coletor()
    resumo = inv.investigar(con, ia_falsa()[0], coletor, inv.pendentes(con), aviso=lambda _: None)
    assert coletor.perfis_lidos == [] and resumo["etapa1_no_teto"] == 1
    assert inv.pendentes(con) == []  # não volta à fila para ser perguntada a cada volta da vigilância
    # Sem o perfil lido, 0,6 não basta para ser do TO (precisa da confiança de quem decide na etapa 1).
    assert con.execute("SELECT do_to FROM investigacoes WHERE username = 'pessoa_comum'").fetchone()[0] == 0


def test_sem_navegador_a_duvida_fica_na_fila(con, municipios):
    _ingerir(con, municipios, [media("C2", "pessoa_comum", "Sem energia de novo, Energisa")])
    resumo = inv.investigar(con, ia_falsa()[0], None, inv.pendentes(con), aviso=lambda _: None)
    assert resumo["em_duvida"] == 1 and resumo["investigados"] == 0
    assert [a["username"] for a in inv.pendentes(con)] == ["pessoa_comum"]  # a vigilância lê o perfil depois


def test_conferir_pelo_ibge_e_pela_confianca(municipios):
    colinas = next(m["ibge_id"] for m in municipios if m["nome"] == "Colinas do Tocantins")

    def conferir(cidade, uf="TO", confianca=0.9):
        return inv.conferir({"cidade": cidade, "uf": uf, "confianca": confianca, "evidencias": "x"}, municipios)

    assert conferir("Colinas")[1:] == (True, colinas)
    assert conferir(None)[1:] == (True, None)  # só o estado (pelo DDD 63, por exemplo)
    assert conferir("Tocantins")[1:] == (True, None)  # o estado, e não o Tocantins de MG
    vila_rica, do_to, ibge = conferir("Vila Rica")  # a DeepSeek disse TO, mas o IBGE só tem Vila Rica em MT
    assert (vila_rica["uf"], do_to, ibge) == ("MT", False, None) and "IBGE" in vila_rica["evidencias"]
    assert conferir("Araguaína", confianca=0.55)[1:] == (False, None)  # palpite por nome de bairro
    assert conferir("Bataguassu", uf="MS")[1:] == (False, None)


def test_palpite_fraco_e_cidade_de_fora_nao_viram_conta_do_to(con, municipios, monkeypatch):
    _ingerir(con, municipios, [
        media("F1", "julio.borgess", "Reunião com a Energisa sobre a Rua Boiadeiro, sem energia há dias"),
        media("F2", "noticias_no_alvorada", "Um poste pegando fogo na rua 5, sem energia"),
    ])
    monkeypatch.setitem(VEREDITOS, "julio.borgess", (
        {"cidade": "Vila Rica", "uf": "TO", "confianca": 0.85, "evidencias": "cita Vila Rica"}, None))
    monkeypatch.setitem(VEREDITOS, "noticias_no_alvorada", (
        {"cidade": None, "uf": None, "confianca": 0.3, "evidencias": "sem cidade"},
        {"cidade": "Araguaína", "uf": "TO", "confianca": 0.55, "evidencias": "bairros Alvorada e Nova Esperança"}))
    resumo = inv.investigar(con, ia_falsa()[0], Coletor(), inv.pendentes(con), aviso=lambda _: None)
    assert resumo["do_to"] == 0 and resumo["de_fora"] == 2 and resumo["etapa2"] == 1
    linhas = {l["username"]: tuple(l)[1:] for l in con.execute(
        "SELECT username, uf, do_to, ibge_id FROM investigacoes")}
    assert linhas == {"julio.borgess": ("MT", 0, None), "noticias_no_alvorada": ("TO", 0, None)}
    assert con.execute("SELECT COUNT(*) FROM conta_municipio WHERE fonte = 'investigacao'").fetchone()[0] == 0
    estados = dict(con.execute("SELECT p.shortcode, b.estado FROM posts_busca b JOIN posts p USING (rede, post_id)"))
    assert estados == {"F1": "descartado", "F2": "descartado"} and bp.candidatos(con) == []


def test_vigia_investiga_e_analisa(con, municipios):
    _ingerir(con, municipios, [media("D1", "luzimanguesnewto", "Dificuldade no atendimento! @energisa em LUZIMANGUES")])
    con.execute("INSERT INTO buscas (fonte, termo, executada_em, n_resultados) "
                "SELECT ?, value, ?, 0 FROM json_each(?)",
                (bp.FONTE, AGORA.isoformat(timespec="seconds"), __import__("json").dumps(bp.TERMOS)))
    con.commit()  # nenhum termo vencido: a volta vai direto à investigação
    ia, _ = ia_falsa()
    coletor = Coletor()
    resumo = vigia.vigiar(con, coletor, "conta2", AGORA + timedelta(hours=1), aviso=lambda _: None, ia=ia,
                          relogio=lambda: AGORA, dormir=lambda _: None,
                          parar=lambda: con.execute("SELECT COUNT(*) FROM investigacoes").fetchone()[0] >= 1)
    assert resumo["investigados"] == 1 and resumo["mencoes"] == 1
    linha = con.execute("SELECT sobre_energia FROM mencoes WHERE post_id = '9D1'").fetchone()
    assert linha[0] == 1 and con.execute("SELECT COUNT(*) FROM reservas").fetchone()[0] == 0
    assert mencoes.FORMATO_BUSCA  # a análise recebe a regra de que fato fora do TO não conta


def test_reprocessar_os_brutos_da_busca(con, municipios, tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    itens = [nav.post_do_site(media("E1", "luzimanguesnewto", "Dificuldade no atendimento! @energisa em LUZIMANGUES"))]
    (tmp_path / f"{AGORA:%Y%m%d-%H%M%S}_busca_posts_nav-1.json").write_text(
        json.dumps({"entrada": {"termo": "energisa palmas"}, "itens": itens}), encoding="utf-8")
    (tmp_path / "20200101-000000_busca_posts_nav-2.json").write_text(  # velho demais: fica de fora
        json.dumps({"entrada": {"termo": "x"}, "itens": itens}), encoding="utf-8")
    resumo = bp.reprocessar_brutos(con)
    assert resumo["arquivos"] == 1 and resumo["a_investigar"] == 1
    assert [a["username"] for a in inv.pendentes(con)] == ["luzimanguesnewto"]
