import json
import re
from datetime import datetime, timedelta, timezone

from radar import comentarios as cm, config, db, mencoes, monitoramento as mon, navegador as nav, perfis, vigia

from conftest import conta, deepseek_falso, post, resposta_deepseek

AGORA = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def no_comentario(pk, texto, curtidas=0, horas_atras=2):
    """Comentário como o site manda (XIGComment), com o autor, que o radar descarta."""
    return {"__typename": "XIGComment", "pk": str(pk), "id": str(pk), "text": texto, "comment_like_count": curtidas,
            "created_at": int((AGORA - timedelta(hours=horas_atras)).timestamp()), "child_comment_count": 0,
            "user": {"username": f"pessoa{pk}", "pk": "99"}}


def pagina(nos, tem_mais):
    return {"data": {"comments_media": {"__typename": "XDTMediaDict", "id": "1", "comments_connection": {
        "edges": [{"node": n} for n in nos], "page_info": {"has_next_page": tem_mais, "end_cursor": "x"}}}}}


def html_do_post(nos, tem_mais=True):
    """A página do post: a legenda (que tem o mesmo formato de um comentário) e os primeiros comentários."""
    legenda = {"require": [{"data": {"xdt_api__v1__media__shortcode__web_info": {"items": [
        {"caption": {"pk": "1", "text": "Falta de energia no Setor Sul", "created_at": 1}}]}}}]}
    return ("<html><script type=\"application/json\" data-sjs>" + json.dumps(legenda) + "</script>"
            "<script type=\"application/json\" data-sjs>" + json.dumps(pagina(nos, tem_mais)) + "</script></html>")


class PaginaComentarios:
    """Faz o papel da página do Instagram na leitura dos comentários."""

    def __init__(self, primeiros, paginas):
        self.primeiros, self.paginas = primeiros, list(paginas)
        self.pedidos = []

    def abrir_comentarios(self, codigo):
        self.pedidos.append(("abrir", codigo))
        return nav.comentarios_do_html(html_do_post(*self.primeiros))

    def mais_comentarios(self):
        self.pedidos.append(("mais", None))
        return nav.comentarios_da_conexao(pagina(*self.paginas.pop(0))) if self.paginas else ([], False)


def test_le_os_comentarios_do_html_e_das_paginas_sem_o_autor(tmp_path):
    nos, tem_mais = nav.comentarios_do_html(html_do_post([no_comentario(1, "sem luz"), no_comentario(2, "aqui tb")]))
    assert [n["text"] for n in nos] == ["sem luz", "aqui tb"] and tem_mais  # a legenda fica de fora

    pagina_falsa = PaginaComentarios(([no_comentario(1, "a"), no_comentario(2, "b")], True),
                                     [([no_comentario(3, "c"), no_comentario(2, "b")], True),
                                      ([no_comentario(4, "d")], False)])
    coletor = nav.Navegador(pagina_falsa, nav.Ritmo(dormir=lambda _: None), raw_dir=tmp_path)
    _, itens = coletor.rodar(config.ATOR_COMENTARIOS, {"directUrls": ["https://www.instagram.com/p/ABC/"],
                                                       "resultsLimit": 150}, "comentarios")
    assert [i["text"] for i in itens] == ["a", "b", "c", "d"]
    assert coletor.leituras == 3  # a página do post e duas levas
    assert set(itens[0]) == {"postUrl", "id", "text", "likesCount", "timestamp", "repliesCount"}  # sem o autor
    assert "pessoa" not in json.dumps(itens)

    limitado = nav.Navegador(PaginaComentarios(([no_comentario(i, str(i)) for i in range(15)], True),
                                               [([no_comentario(99, "x")], True)]),
                             nav.Ritmo(dormir=lambda _: None), raw_dir=tmp_path)
    assert len(limitado.comentarios("https://www.instagram.com/p/ABC/", 10)) == 10
    assert limitado.leituras == 1  # o limite chegou na primeira página


def _mencao(con, municipios, codigo, comentarios, dias=1, sobre_energia=1, cita_energisa=0, conta_="portal"):
    if not con.execute("SELECT 1 FROM contas WHERE username = ?", (conta_,)).fetchone():
        conta(con, municipios, conta_, [])
    item = post(dias, legenda="Falta de energia", codigo=codigo) | {"commentsCount": comentarios}
    item["timestamp"] = (AGORA - timedelta(days=dias)).isoformat().replace("+00:00", "Z")
    perfis.ingerir_posts(con, conta_, [item])
    con.execute("INSERT INTO mencoes (rede, post_id, sobre_energia, cita_energisa, tema, sentimento, alcance, resumo, "
                "confianca, modelo, analisada_em) VALUES ('instagram', ?, ?, ?, 'falta_energia', 'negativo', "
                "'local', 'Bairro sem luz', 0.9, 'm', 'x')", (f"id-{codigo}", sobre_energia, cita_energisa))
    con.commit()
    return f"id-{codigo}"


def test_quais_posts_tem_os_comentarios_lidos_e_quando(con, municipios):
    _mencao(con, municipios, "NOVO", 40, dias=0.1)                        # 2,4 h: cedo demais
    _mencao(con, municipios, "MADURO", 40, dias=1)
    _mencao(con, municipios, "VAGA", 12, dias=1, sobre_energia=0, cita_energisa=1)  # cita a Energisa: entra
    _mencao(con, municipios, "VELHO", 90, dias=40)                        # fora do último mês
    _mencao(con, municipios, "SEM", 0, dias=1)                            # sem comentários
    _mencao(con, municipios, "PROPRIA", 30, dias=1, conta_="energisatocantins")  # conta da Energisa
    assert [p["shortcode"] for p in mencoes.comentarios_vencidos(con, AGORA)] == ["MADURO", "VAGA"]

    mencoes.marcar_comentarios_lidos(con, [{"rede": "instagram", "post_id": "id-MADURO"}])
    con.execute("UPDATE mencoes SET comentarios_lidos_em = ? WHERE post_id = 'id-MADURO'",
                ((AGORA - timedelta(hours=20)).isoformat(),))
    assert "MADURO" not in [p["shortcode"] for p in mencoes.comentarios_vencidos(con, AGORA)]
    # Perto das 48 h, relê se os comentários cresceram 30% ou mais.
    depois = AGORA + timedelta(hours=30)
    assert "MADURO" not in [p["shortcode"] for p in mencoes.comentarios_vencidos(con, depois)]
    con.execute("UPDATE posts SET comentarios = 60 WHERE post_id = 'id-MADURO'")
    assert "MADURO" in [p["shortcode"] for p in mencoes.comentarios_vencidos(con, depois)]
    mencoes.marcar_comentarios_lidos(con, [{"rede": "instagram", "post_id": "id-MADURO"}])
    assert "MADURO" not in [p["shortcode"] for p in mencoes.comentarios_vencidos(con, depois)]  # uma releitura só


def deepseek_comentarios():
    """Marca "sem energia/luz" como negativo e falta de energia, com o local depois de "no"; responde a reação."""
    def responder(params):
        sistema, usuario = params["messages"][0]["content"], params["messages"][1]["content"]
        if sistema == mencoes.INSTRUCOES_REACAO:
            return resposta_deepseek({"reacao": "negativa", "resumo": "Moradores reclamam da falta de luz."})
        saida = []
        for linha in usuario.splitlines():
            achado = re.match(r"\[(\d+)\] (.*)", linha)
            if not achado:
                continue
            texto = achado.group(2)
            if "sem energia" in texto or "sem luz" in texto:
                local = re.search(r" no (.+)$", texto)
                saida.append({"n": int(achado.group(1)), "sentimento": "negativo", "relato": "falta_energia",
                              "local": local.group(1) if local else None})
            else:
                saida.append({"n": int(achado.group(1)), "sentimento": "neutro", "relato": None, "local": None})
        return resposta_deepseek({"comentarios": saida})
    return deepseek_falso(responder)


def _comentarios(con, post_id, textos, horas_atras=1):
    for i, texto in enumerate(textos):
        con.execute("INSERT INTO comentarios (rede, comentario_id, post_id, texto, curtidas, publicado_em) VALUES "
                    "('instagram', ?, ?, ?, 0, ?)", (f"{post_id}-{i}", post_id, texto,
                                                     (datetime.now(timezone.utc) - timedelta(hours=horas_atras))
                                                     .strftime("%Y-%m-%dT%H:%M:%S.000Z")))
    con.commit()


def test_analisa_em_lotes_e_alerta_relatos_no_mesmo_lugar(con, municipios):
    pid = _mencao(con, municipios, "P1", 50)
    gurupi = next(m for m in municipios if m["nome"] == "Gurupi")["ibge_id"]
    con.execute("INSERT INTO mencao_municipio (rede, post_id, ibge_id) VALUES ('instagram', ?, ?)", (pid, gurupi))
    textos = ["sem energia no Setor Sul", "aqui também sem luz no Setor Sul", "sem energia no setor sul",
              "Deus abençoe", "sem luz no Centro"] + [f"comentário {i}" for i in range(40)]
    _comentarios(con, pid, textos)
    ia, chamadas = deepseek_comentarios()
    resumo = cm.rodar(con, ia, aviso=lambda _: None)
    assert resumo["comentarios"] == 45 and resumo["lotes"] == 2 and len(chamadas) == 2  # lotes de 40
    assert cm.pendentes(con) == []
    linha = con.execute("SELECT sentimento, relato, local, ibge_id FROM comentario_analise WHERE comentario_id = ?",
                        (f"{pid}-0",)).fetchone()
    assert tuple(linha) == ("negativo", "falta_energia", "Setor Sul", gurupi)  # o bairro fica na cidade do post

    avisos = []
    assert cm.alertas_de_relatos(con, aviso=avisos.append) == 1  # 3 no Setor Sul; o Centro tem 1 só
    assert "3 comentários relatam falta de energia em Setor Sul (Gurupi)" in avisos[0]
    assert (vigia.ARQUIVO_ALERTAS.read_text(encoding="utf-8").count("Setor Sul")) == 1
    assert cm.alertas_de_relatos(con, aviso=avisos.append) == 0  # um por lugar por dia

    r = cm.resumo(con, dias=30)
    assert r["comentarios"] == 45 and r["negativos_pct"] == 9 and r["relatos"] == {"falta_energia": 4}  # 4 de 45
    assert r["lugares"]["Setor Sul (Gurupi)"] == 3


def test_relatos_antigos_nao_alertam(con, municipios):
    pid = _mencao(con, municipios, "P2", 10)
    _comentarios(con, pid, ["sem energia no Setor Sul"] * 3, horas_atras=30)  # fora da janela de 12 h
    cm.rodar(con, deepseek_comentarios()[0], aviso=lambda _: None)
    assert cm.alertas_de_relatos(con, aviso=lambda _: None) == 0


class ColetorComComentarios:
    """Coletor da vigilância que lê comentários (e não acha páginas nem posts)."""

    def __init__(self, textos):
        self.textos = textos
        self.lidos = []

    def comentarios(self, url, maximo):  # existe para a vigilância saber que este coletor lê comentários
        raise AssertionError("a vigilância lê pelo rodar, como a Apify")

    def rodar(self, ator, entrada, rotulo, max_usd=None):
        assert ator == config.ATOR_COMENTARIOS
        self.lidos += entrada["directUrls"]
        return "nav", [{"postUrl": u, "id": f"c{i}", "text": t, "likesCount": 0,
                        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
                       for u in entrada["directUrls"] for i, t in enumerate(self.textos)]


def test_vigia_le_os_comentarios_da_mencao_vencida_e_analisa(con, municipios):
    agora = datetime.now(timezone.utc)
    pid = _mencao(con, municipios, "V1", 30)
    con.execute("UPDATE posts SET publicado_em = ? WHERE post_id = ?",
                ((agora - timedelta(hours=10)).isoformat().replace("+00:00", "Z"), pid))
    con.commit()
    coletor = ColetorComComentarios(["sem energia no Setor Sul", "sem luz no Setor Sul", "sem energia no Setor Sul"])
    ia, _ = deepseek_comentarios()
    resumo = vigia.vigiar(con, coletor, "conta2", agora + timedelta(hours=1), aviso=lambda _: None, ia=ia,
                          relogio=lambda: agora, dormir=lambda _: None, parar=lambda: len(coletor.lidos) >= 1)
    assert coletor.lidos == ["https://www.instagram.com/p/V1/"]
    assert resumo["comentarios"] == 3 and resumo["relatos"] == 3
    linha = con.execute("SELECT n_comentarios_lidos, reacao FROM mencoes WHERE post_id = ?", (pid,)).fetchone()
    assert tuple(linha) == (3, "negativa")
    assert con.execute("SELECT COUNT(*) FROM reservas").fetchone()[0] == 0
    assert "Setor Sul" in vigia.ARQUIVO_ALERTAS.read_text(encoding="utf-8")


def test_coleta_pela_fila_grava_sem_autor(con, municipios, tmp_path):
    pid = _mencao(con, municipios, "ABC", 3)
    pagina_falsa = PaginaComentarios(([no_comentario(1, "sem luz"), no_comentario(2, "aqui tb")], False), [])
    coletor = nav.Navegador(pagina_falsa, nav.Ritmo(dormir=lambda _: None), raw_dir=tmp_path)
    fila = mencoes.sem_comentarios(con, 10)
    assert mon.coletar_comentarios(con, coletor, fila, 150, aviso=lambda _: None) == 2
    mencoes.marcar_comentarios_lidos(con, fila)
    assert [l[0] for l in con.execute("SELECT texto FROM comentarios ORDER BY texto")] == ["aqui tb", "sem luz"]
    linha = con.execute("SELECT n_comentarios_lidos, comentarios_na_leitura FROM mencoes WHERE post_id = ?",
                        (pid,)).fetchone()
    assert tuple(linha) == (2, 3)
    colunas = {l[1] for l in con.execute("PRAGMA table_info(comentarios)")}
    assert not colunas & {"username", "autor", "usuario"}
    assert db  # o banco de teste é o mesmo do radar
