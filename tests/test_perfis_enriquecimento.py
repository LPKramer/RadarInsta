import json

from radar import config, enriquecimento as enr, perfis

from conftest import ApifyFalso, perfil, post


def test_normalizar_perfil_resume_posts():
    item = perfil("gurupinoticias", latestPosts=[post(1, 200, 20), post(5, -1, 10), post(60)])
    conta = perfis.normalizar(item)
    assert conta["username"] == "gurupinoticias"
    assert conta["completo"] is True
    assert conta["posts_30d"] == 2
    # Curtidas ocultas (-1) contam como zero.
    assert conta["engajamento_medio"] == (220 + 10 + 110) / 3
    recentes = json.loads(conta["posts_recentes"])
    assert len(recentes) == 3 and recentes[0]["curtidas"] == 200


def test_normalizar_sem_posts_e_item_invalido():
    conta = perfis.normalizar(perfil("x", latestPosts=[]))
    assert conta["ultimo_post_em"] is None and conta["posts_30d"] is None
    assert perfis.normalizar({"error": "not found", "username": "x"}) is None
    assert perfis.normalizar({}) is None


def test_relacionadas_ignora_privadas():
    item = perfil("a", relatedProfiles=[{"username": "B", "full_name": "Bê"}, {"username": "c", "is_private": True}])
    assert perfis.relacionadas(item) == [("b", "Bê")]


def test_username_de_url():
    assert perfis.username_de_url("https://www.instagram.com/gurupinoticias/") == "gurupinoticias"
    assert perfis.username_de_url("https://instagram.com/Palmas.Agora/reels/") == "palmas.agora"
    assert perfis.username_de_url("https://www.instagram.com/p/Cx123/") is None
    assert perfis.username_de_url("https://example.com/x") is None


def test_ingerir_nao_apaga_dados_conhecidos(con, municipios):
    perfis.ingerir(con, perfil("gurupinoticias", biography="Notícias de Gurupi"), "busca", municipios)
    perfis.ingerir_username(con, "gurupinoticias", "relacionada", profundidade=2)
    linha = con.execute("SELECT * FROM contas WHERE username = 'gurupinoticias'").fetchone()
    assert linha["bio"] == "Notícias de Gurupi"
    assert linha["origem"] == "busca" and linha["profundidade"] == 0 and linha["completo"] == 1
    fontes = {r["fonte"] for r in con.execute("SELECT fonte FROM conta_municipio")}
    assert fontes == {"bio"}


def conta(**extra):
    base = perfis.normalizar(perfil("palmasnoticias"))
    return base | {"origem": "busca"} | extra


def test_motivo_descarte():
    assert enr.motivo_descarte(conta()) is None
    assert enr.motivo_descarte(conta(privada=1)) == "privada"
    assert enr.motivo_descarte(conta(seguidores=config.SEGUIDORES_MIN - 1)) == "poucos_seguidores"
    assert enr.motivo_descarte(conta(ultimo_post_em="2020-01-01T00:00:00+00:00")) == "inativa"
    loja = conta(username="modafeminina", bio="Moda feminina. Pedidos no WhatsApp", nome="Boutique Bela")
    assert enr.motivo_descarte(loja) == "comercial"
    # Sinal de notícia vence sinal de comércio.
    assert enr.motivo_descarte(loja | {"username": "gurupinoticias"}) is None
    assert enr.motivo_descarte(loja | {"bio": "Portal de notícias e loja virtual"}) is None
    # Contas da equipe e contas ainda sem perfil não são descartadas por regra.
    assert enr.motivo_descarte(loja | {"origem": "manual"}) is None
    assert enr.motivo_descarte({"username": "x", "completo": 0, "origem": "relacionada"}) is None


def test_enriquecer_marca_nao_encontradas(con):
    perfis.ingerir_username(con, "existe", "relacionada", 1)
    perfis.ingerir_username(con, "sumiu", "relacionada", 1)
    apify = ApifyFalso({config.ATOR_PERFIL: [perfil("existe", biography="Notícias de Palmas")]})
    resumo = enr.enriquecer(con, apify, aviso=lambda _: None)
    assert resumo["enriquecidas"] == 1 and resumo["nao_encontradas"] == 1
    assert sorted(apify.chamadas[0][1]["usernames"]) == ["existe", "sumiu"]
    linhas = {r["username"]: r for r in con.execute("SELECT * FROM contas")}
    assert linhas["existe"]["completo"] == 1 and linhas["existe"]["profundidade"] == 1
    assert linhas["existe"]["origem"] == "relacionada"
    assert linhas["sumiu"]["descartada"] == "nao_encontrada"
    assert enr.pendentes(con) == []
