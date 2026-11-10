from radar import classificacao as cl, enriquecimento, mencoes as mc, noticias, relatorio

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Energia cai várias vezes durante a noite em Araguaína e região - AF Notícias</title>
  <link>https://news.google.com/rss/articles/A1?oc=5</link><guid isPermaLink="false">A1</guid>
  <pubDate>Tue, 20 Oct 2026 13:05:00 GMT</pubDate><source url="https://www.afnoticias.com.br">AF Notícias</source></item>
<item><title>Aneel aprova reajuste de 7,92% na tarifa da Energisa Tocantins - CNN Brasil</title>
  <link>https://news.google.com/rss/articles/A2?oc=5</link><guid isPermaLink="false">A2</guid>
  <pubDate>Tue, 30 Jun 2026 10:00:00 GMT</pubDate><source url="https://www.cnnbrasil.com.br">CNN Brasil</source></item>
<item><title>Grupo Energisa conclui venda de ativos de transmissão - Energisa</title>
  <link>https://news.google.com/rss/articles/A3?oc=5</link><guid isPermaLink="false">A3</guid>
  <pubDate>Wed, 09 Sep 2026 10:00:00 GMT</pubDate><source url="https://www.energisa.com.br">Energisa</source></item>
<item><title>Prefeitura de Gurupi abre matrículas - AF Notícias</title>
  <link>https://news.google.com/rss/articles/A4?oc=5</link><guid isPermaLink="false">A4</guid>
  <pubDate>Wed, 21 Oct 2026 10:00:00 GMT</pubDate><source url="https://www.afnoticias.com.br">AF Notícias</source></item>
</channel></rss>""".encode()


def test_ler_rss_tira_o_veiculo_do_titulo():
    itens = noticias.ler_rss(RSS)
    assert itens[0] == {"id": "A1", "titulo": "Energia cai várias vezes durante a noite em Araguaína e região",
                        "veiculo": "AF Notícias", "dominio": "afnoticias.com.br", "site": "https://www.afnoticias.com.br",
                        "link": "https://news.google.com/rss/articles/A1?oc=5", "publicado_em": "2026-10-20T13:05:00Z"}


def test_consultas_sem_repeticao(municipios):
    lista = noticias.consultas(municipios)
    assert len(lista) == len(set(lista)) and 'Energisa "Palmas"' in lista and '"falta de energia" "Palmas" Tocantins' in lista
    assert 'Energisa "Peixe"' not in lista  # município pequeno: só as consultas do estado
    assert "when%3A90d" in noticias.endereco("Energisa", 90)


def test_coletar_grava_so_o_tocantins_e_nao_repete(con, municipios):
    resumo = noticias.coletar(con, 30, aviso=lambda _: None, baixar_=lambda url: RSS, dormir=lambda _: None)
    assert resumo["consultas"] == len(noticias.consultas(municipios))
    assert resumo["novas"] == 3  # a venda de ativos do grupo não cita o TO
    posts = {l[0] for l in con.execute("SELECT post_id FROM posts WHERE rede = 'web'")}
    assert posts == {"A1", "A2", "A4"}
    # Só as que passam no filtro de palavras vão para a IA; a de matrículas fica de fora.
    assert sorted(p["post_id"] for p in noticias.candidatos(con)) == ["A1", "A2"]
    # Os veículos não entram no funil das contas do Instagram.
    assert not [c for c in cl.pendentes(con) if c["username"] == "afnoticias.com.br"]
    assert not [c for c in enriquecimento.pendentes(con) if c["username"] == "afnoticias.com.br"]


def test_materia_analisada_entra_no_relatorio(con, municipios):
    noticias.coletar(con, 30, aviso=lambda _: None, baixar_=lambda url: RSS, dormir=lambda _: None)
    post = next(p for p in noticias.candidatos(con) if p["post_id"] == "A1")
    texto = mc.descrever(post)
    assert "Site: AF Notícias (afnoticias.com.br)" in texto and "Tocantins" in texto
    mc.salvar(con, post, mc.validar({"sobre_energia": True, "tema": "falta_energia", "sentimento": "negativo",
                                     "alcance": "regional", "municipios": ["Araguaína"], "resumo": "Quedas.",
                                     "confianca": 0.9}), municipios, "m")
    con.commit()
    linha = relatorio.carregar(con).iloc[0]
    assert (linha["conta"], linha["rede"], linha["tipo_conta"], linha["municipios"]) == (
        "AF Notícias", "web", "site_noticias", "Araguaína")
    assert noticias.situacao(con)["negativas"] == 1


def test_outra_distribuidora_e_veiculo_de_fora_ficam_de_fora(con, municipios):
    assert not noticias.do_tocantins("Moradores protestam em frente a ponto da Copel de Palmas", municipios)
    assert noticias.do_tocantins("Moradores de Palmas reclamam de falta de energia", municipios)
    from radar import db
    db.upsert_conta(con, {"username": "afnoticias.com.br", "nome": "AF", "completo": 1, "origem": "x"}, rede="web")
    con.execute("UPDATE contas SET descartada = 'fora_do_to' WHERE rede = 'web' AND username = 'afnoticias.com.br'")
    resumo = noticias.ingerir(con, noticias.ler_rss(RSS), municipios)
    assert resumo["novas"] == 1  # só a da CNN; as duas do veículo marcado ficam de fora
