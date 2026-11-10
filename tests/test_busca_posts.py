from datetime import datetime, timedelta, timezone

from radar import busca_posts as bp, db, mencoes, navegador as nav, vigia

from conftest import analise, conta, deepseek_falso, resposta_deepseek

AGORA = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


def media(codigo, dono, legenda, dias=1, local=None):
    """Post como vem na grade da busca por palavra-chave do site."""
    return {"pk": f"9{len(codigo)}{codigo}", "code": codigo, "media_type": 1, "caption": {"text": legenda},
            "taken_at": int((AGORA - timedelta(days=dias)).timestamp()), "like_count": 10, "comment_count": 2,
            "user": {"username": dono}, "location": {"name": local} if local else None}


class Coletor:
    """Coletor da vigilância com a busca de posts: devolve as mesmas mídias para todo termo."""

    def __init__(self, medias):
        self.medias = medias
        self.termos = []

    def buscar_posts(self, termo):
        self.termos.append(termo)
        return [nav.post_do_site(m) for m in self.medias]

    def rodar(self, ator, entrada, rotulo, max_usd=None):
        return "falso", []


def test_le_os_posts_da_grade_da_busca():
    conexao = {"edges": [{"node": {"items": [media("A1", "x", "a"), media("B2", "y", "b")]}},
                         {"node": {"items": [media("A1", "x", "a")]}}]}  # repetido em outra seção
    assert [m["code"] for m in nav.medias_da_busca(conexao)] == ["A1", "B2"]
    item = nav.post_do_site(media("A1", "Portal_X", "Falta de energia em Palmas"))
    assert item["ownerUsername"] == "portal_x" and item["caption"] == "Falta de energia em Palmas"


def test_triagem_em_tres_destinos(con, municipios):
    conta(con, municipios, "jornal_gurupi", [], municipios=["Gurupi"])  # conta do TO pela IA
    medias = [
        media("OK1", "moradora_x", "Energisa deixou o setor sem energia em Araguaína de novo"),
        media("OK2", "jornal_gurupi", "Queda de energia hoje à tarde"),  # a conta é do TO
        media("OK3", "fulano", "Mais um dia sem luz, Energisa!", local="Palmas, Tocantins"),
        media("EN1", "energisatocantins", "Energisa Tocantins informa manutenção programada em Palmas"),
        media("EN2", "grupoenergisa", "Energisa Tocantins: obras de rede"),
        media("VE1", "moradora_y", "Falta de energia em Gurupi", dias=60),
        media("SE1", "loja_palmas", "Promoção de sapatos em Palmas"),
        media("IN1", "mironbataguassu", "Chegamos no limite do descaso da Energisa, sem energia"),  # sem cidade
        media("FO2", "gilmarmt", "Energisa MT sem energia em Cuiabá e no Tocantins também"),
        media("IN2", "taguatinga_df", "Falta de energia em Taguatinga desde cedo"),  # homônima, sem sinal de TO
    ]
    resumo = bp.ingerir(con, "energisa palmas", [nav.post_do_site(m) for m in medias], municipios, AGORA)
    assert resumo["do_to"] == 3 and resumo["a_investigar"] == 2 and resumo["relevantes"] == 5
    assert resumo["energisa"] == 2 and resumo["antigo"] == 1 and resumo["sem_energia"] == 1
    assert resumo["outra_distribuidora"] == 1
    achados = dict(con.execute("SELECT p.shortcode, b.estado FROM posts_busca b JOIN posts p USING (rede, post_id)"))
    assert achados == {"OK1": "do_to", "OK2": "do_to", "OK3": "do_to", "IN1": "a_investigar",
                       "IN2": "a_investigar"}  # quem decide se as contas sem pista são do TO é a investigação
    assert con.execute("SELECT origem FROM contas WHERE username = 'moradora_x'").fetchone()[0] == bp.ORIGEM
    assert not con.execute("SELECT 1 FROM contas WHERE username LIKE '%energisa%'").fetchone()
    # De novo (outro termo, mesmo post): não conta como novo.
    assert bp.ingerir(con, "energisa", [nav.post_do_site(medias[0])], municipios, AGORA)["novos"] == 0
    # A IA recebe a regra de que fato fora do TO não conta (a Energisa atende outros estados).
    candidatos = bp.candidatos(con)
    assert len(candidatos) == 3 and all(mencoes.FORMATO_BUSCA in mencoes.descrever(p) for p in candidatos)


def test_termos_vencem_e_sao_reservados_entre_as_contas(tmp_path):
    caminho = tmp_path / "radar.db"
    a, b = db.conectar(caminho), db.conectar(caminho)
    assert bp.vencidos(a, AGORA) == bp.TERMOS  # nunca buscados: todos, na ordem
    t1, t2 = bp.reservar(a, "conta2", AGORA), bp.reservar(b, "conta3", AGORA)
    assert (t1, t2) == (bp.TERMOS[0], bp.TERMOS[1])  # dois trabalhadores, termos diferentes
    bp.buscar(a, Coletor([]), t1, AGORA)
    bp.liberar(a, t1)
    assert t1 not in bp.vencidos(a, AGORA + timedelta(hours=5))
    assert t1 in bp.vencidos(a, AGORA + timedelta(hours=7))  # a cada 6 h
    assert bp.vencidos(a, AGORA, intervalo_h=0) == []  # 0 desliga


def test_vigia_busca_posts_vencidos_e_analisa(con, municipios):
    coletor = Coletor([media("OK1", "moradora_x", "Energisa deixou o setor sem energia em Araguaína", dias=0)])
    respostas = lambda params: resposta_deepseek(analise(municipios=["Araguaína"]))  # noqa: E731
    ia, chamadas = deepseek_falso(respostas)
    resumo = vigia.vigiar(con, coletor, "conta2", AGORA + timedelta(hours=1), aviso=lambda _: None, ia=ia,
                          relogio=lambda: AGORA, dormir=lambda _: None, parar=lambda: len(coletor.termos) >= 3)
    assert coletor.termos == bp.TERMOS[:3]
    assert resumo["posts_buscados"] == 1 and len(chamadas) == 1  # o post repetido não vai de novo à IA
    assert con.execute("SELECT COUNT(*) FROM reservas").fetchone()[0] == 0
    linha = con.execute("SELECT sobre_energia, sentimento FROM mencoes").fetchone()
    assert tuple(linha) == (1, "negativo")
    assert bp.situacao(con)["negativos"] == 1
