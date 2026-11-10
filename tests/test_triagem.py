from types import SimpleNamespace

import pytest

from radar import mencoes as mc, stories, triagem
from radar.classificacao import ClassificacaoFalhou

from conftest import conta
from test_stories import ColetorFalso, item_story


def resposta(conteudo: str, entrada=500, saida=12, cache=300):
    return SimpleNamespace(model="deepseek-flash", usage=SimpleNamespace(
        prompt_tokens=entrada, completion_tokens=saida, prompt_cache_hit_tokens=cache),
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=conteudo))])


class DeepSeekFalso:
    """Marca os stories com 'apagad' no texto (como a IA faria com "setor todo apagado desde ontem")."""

    def __init__(self, falhar=False):
        self.pedidos = []
        self.falhar = falhar
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **params):
        if self.falhar:
            raise ConnectionError("sem rede")
        usuario = params["messages"][1]["content"]
        self.pedidos.append(usuario)
        linhas = [l for l in usuario.splitlines() if l.startswith("[")]
        marcados = [int(l[1:l.index("]")]) for l in linhas if "apagad" in l.lower()]
        return resposta('{"energia": %s}' % marcados)


def test_interpretar_aceita_so_numeros_do_lote():
    assert triagem.interpretar({"energia": [1, "3", 9, "x"]}, 3) == {0, 2}
    assert triagem.interpretar({"energia": []}, 3) == set()
    with pytest.raises(ClassificacaoFalhou):
        triagem.interpretar({"outra": [1]}, 3)


def test_precisa_so_quem_tem_texto_e_nao_passou_no_filtro():
    assert triagem.precisa("Setor Sul todo apagado desde ontem e ninguém resolve")
    assert not triagem.precisa("Falta de energia no centro")  # o filtro já pega
    assert not triagem.precisa("bom dia")  # quase sem texto: não há o que ler


def test_vigia_tria_os_stories_da_pagina_e_guarda_a_foto_do_marcado(con, municipios, tmp_path):
    conta(con, municipios, "portal", [])
    textos = {"https://cdn/1_g.jpg": "URGENTE Falta de energia atinge bairros de Araguaína",
              "https://cdn/2_g.jpg": "Setor Sul todo apagado desde ontem e ninguém resolve",
              "https://cdn/3_g.jpg": "Promoção de sapatos só hoje na loja do centro",
              "https://cdn/4_g.jpg": "oi"}
    coletor = ColetorFalso([item_story(1), item_story(2), item_story(3), item_story(4)])
    ler_texto = lambda caminho: textos[coletor.baixados[-1]]  # noqa: E731
    ia = DeepSeekFalso()
    resumo = stories.coletar(con, coletor, "portal", pasta=tmp_path, ler_texto=ler_texto,
                             triar=lambda t: triagem.triar_textos(ia, t))

    assert resumo == {"stories": 4, "candidatos": 2, "triados": 2}  # o do filtro e o curto não vão à triagem
    assert len(ia.pedidos) == 1 and "[1] Setor Sul" in ia.pedidos[0] and "[2] Promoção" in ia.pedidos[0]
    assert (tmp_path / "portal" / "1.jpg").exists() and (tmp_path / "portal" / "2.jpg").exists()
    assert not (tmp_path / "portal" / "3.jpg").exists()
    assert dict(con.execute("SELECT post_id, energia FROM triagem").fetchall()) == {"2": 1, "3": 0}
    assert sorted(p["post_id"] for p in mc.candidatos(con)) == ["1", "2"]  # o marcado vai para a análise completa
    assert [p["post_id"] for p in triagem.marcados_sem_analise(con)] == ["2"]


def test_triagem_que_falha_nao_para_a_coleta(con, municipios, tmp_path):
    conta(con, municipios, "portal", [])
    coletor = ColetorFalso([item_story(1)])
    ia = DeepSeekFalso(falhar=True)
    resumo = stories.coletar(con, coletor, "portal", pasta=tmp_path,
                             ler_texto=lambda _: "Setor Sul todo apagado desde ontem e ninguém resolve",
                             triar=lambda t: triagem.triar_textos(ia, t))
    assert resumo == {"stories": 1, "candidatos": 0}
    assert [s["post_id"] for s in triagem.pendentes(con)] == ["1"]  # fica para o triar-stories


def test_rodar_os_guardados_em_lotes(con, municipios, tmp_path):
    conta(con, municipios, "portal", [])
    textos = ["Árvore caída sobre a rede elétrica da rua 7" if i % 10 == 0 else f"Sorteio número {i} da loja"
              for i in range(45)]
    coletor = ColetorFalso([item_story(i) for i in range(45)])
    ordem = iter(textos)
    stories.coletar(con, coletor, "portal", pasta=tmp_path, ler_texto=lambda _: next(ordem))
    a_triar = triagem.pendentes(con)
    assert len(a_triar) == 40  # os 5 da árvore passam no filtro ("sobre a rede") e vão direto à análise

    ia = DeepSeekFalso()
    resumo = triagem.rodar(con, ia, a_triar, aviso=lambda _: None)
    assert resumo["triados"] == 40 and resumo["lotes"] == 2 and len(ia.pedidos) == 2
    assert resumo["entrada"] == 1000 and resumo["saida"] == 24 and resumo["cache"] == 600
    assert triagem.pendentes(con) == []
