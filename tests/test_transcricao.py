from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from radar import config, mencoes, monitoramento as mon, navegador as nav, painel, perfis, radios, relatorio
from radar import transcricao as tr, vigia

from conftest import ApifyFalso, analise, conta, deepseek_falso, resposta_deepseek

# O manifesto DASH como a grade manda (endereços fictícios): a faixa de áudio de menor taxa é a que a transcrição baixa.
OE = format(int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp()), "x")
MANIFESTO = f"""<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT1M7.5S"><Period>
<AdaptationSet contentType="video" mimeType="video/mp4"><Representation bandwidth="900000">
<BaseURL>https://cdn.exemplo/v/video.mp4?oe={OE}&amp;x=1</BaseURL></Representation></AdaptationSet>
<AdaptationSet contentType="audio" mimeType="audio/mp4">
<Representation bandwidth="64000"><BaseURL>https://cdn.exemplo/a/alto.mp4?oe={OE}&amp;x=1</BaseURL></Representation>
<Representation bandwidth="48000"><BaseURL>https://cdn.exemplo/a/baixo.mp4?oe={OE}&amp;x=1</BaseURL></Representation>
</AdaptationSet></Period></MPD>"""


def no_video(pk, dias_atras=0.5, **extra) -> dict:
    """Vídeo da grade do perfil, como o site manda."""
    quando = datetime.now(timezone.utc) - timedelta(days=dias_atras)
    return {"pk": str(pk), "code": f"V{pk}", "media_type": 2, "taken_at": int(quando.timestamp()),
            "caption": {"text": f"Programa {pk}"}, "user": {"username": "radiox"},
            "video_versions": [{"url": "https://cdn.exemplo/v/grande.mp4", "width": 1080, "height": 1920},
                               {"url": "https://cdn.exemplo/v/pequeno.mp4", "width": 480, "height": 854}],
            "video_dash_manifest": MANIFESTO} | extra


def _radio(con, municipios) -> None:
    conta(con, municipios, "radiox", [], tipo="jornalista_radialista")
    conta(con, municipios, "portaly", [])
    radios.aprovar(con, ["radiox"])


def _na_fila(con, *pks) -> None:
    itens = [nav.post_do_site(no_video(pk)) for pk in pks]
    perfis.ingerir_posts(con, "radiox", itens)
    tr.enfileirar(con, "radiox", itens)
    con.commit()


class Falsos:
    """Download, conversão e transcrição de mentira: cada post tem a sua fala (ou o seu erro)."""

    def __init__(self, falas, erros=None):
        self.falas, self.erros, self.atual = falas, erros or {}, None

    def baixar(self, url, destino):
        erro = self.erros.get(destino.stem.split("_", 1)[1])
        if erro:
            raise erro
        destino.write_bytes(b"audio")
        return 5

    def converter(self, caminho):
        assert caminho.exists()
        self.atual = caminho.stem.split("_", 1)[1]
        return [0.0] * (tr.TAXA * 90)  # 90 segundos

    def transcritor(self, audio):
        return [tr.Trecho(i * 10, i * 10 + 9, t) for i, t in enumerate(self.falas.get(self.atual, []))]

    def rodar(self, con, cliente=None):
        return tr.rodar(con, cliente, self.transcritor, aviso=lambda _: None, baixar=self.baixar,
                        converter=self.converter)


def test_a_grade_traz_o_audio_e_a_duracao():
    item = nav.post_do_site(no_video(1))
    assert item["audioUrl"] == f"https://cdn.exemplo/a/baixo.mp4?oe={OE}&x=1"  # o &amp; do XML já desfeito
    assert item["videoUrl"] == "https://cdn.exemplo/v/pequeno.mp4"
    assert item["videoDuration"] == 67.5 and item["type"] == "Video"
    assert tr.expira(item["audioUrl"])[:10] == (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    assert nav.video_do_site({"media_type": 2, "video_dash_manifest": "<quebrado"}) == {
        "videoUrl": None, "audioUrl": None, "videoDuration": None}
    assert "audioUrl" not in nav.post_do_site({"pk": "2", "code": "I2", "media_type": 1})


def test_a_vigilancia_poe_na_fila_so_os_videos_recentes_das_radios(con, municipios):
    _radio(con, municipios)
    itens = [nav.post_do_site(no_video(1)),
             nav.post_do_site(no_video(2, dias_atras=9)),  # velho demais
             nav.post_do_site(no_video(3)) | {"ownerUsername": "portaly"}]  # não é rádio
    coletor = ApifyFalso({config.ATOR_POSTS: itens, config.ATOR_PERFIL: []})
    r = mon.coletar_novos(con, coletor, mon.monitoradas(con), aviso=lambda _: None, com_stories=False)
    assert r["videos_na_fila"] == 1
    fila = tr.fila(con)
    assert [f["post_id"] for f in fila] == ["1"] and fila[0]["midia_url"].startswith("https://cdn.exemplo/a/baixo")
    assert fila[0]["duracao"] == 67.5
    # Na visita seguinte o endereço é renovado, sem duplicar; o vídeo já transcrito não volta para a fila.
    novo = nav.post_do_site(no_video(1)) | {"audioUrl": f"https://cdn.exemplo/a/novo.mp4?oe={OE}"}
    assert tr.enfileirar(con, "radiox", [novo]) == 0
    assert tr.fila(con)[0]["midia_url"].startswith("https://cdn.exemplo/a/novo")
    con.execute("UPDATE transcricoes SET estado = 'feita', midia_url = NULL")
    tr.enfileirar(con, "radiox", [novo])
    assert tr.fila(con) == []


def test_transcreve_guarda_so_os_trechos_de_energia_e_alerta(con, municipios, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIDIA_DIR", tmp_path / "midia")
    _radio(con, municipios)
    _na_fila(con, 1, 2, 3)
    falsos = Falsos({
        "1": ["Bom dia, Gurupi!", "Começa agora o Jornal da Manhã.", "O setor Waldir Lins está sem energia desde ontem.",
              "Liga pra gente no 63 99999-1234.", "Agora a previsão do tempo.", "Calor de 38 graus."],
        "2": ["Promoção de colchões na loja do centro."],
    })
    pedidos = []

    def responder(params):
        pedidos.append(params["messages"][1]["content"])
        return resposta_deepseek(analise(resumo="Setor Waldir Lins, em Gurupi, sem energia desde ontem."))

    cliente, _ = deepseek_falso(responder)
    resumo = falsos.rodar(con, cliente)
    assert (resumo["feita"], resumo["sem_fala"], resumo["com_energia"], resumo["mencoes"]) == (2, 1, 1, 1)
    linha = con.execute("SELECT * FROM transcricoes WHERE post_id = '1'").fetchone()
    assert (linha["estado"], linha["energia"], linha["midia_url"], linha["segundos"]) == ("feita", 1, None, 90)
    # Só a fala de energia, com um vizinho de cada lado e o telefone mascarado; a previsão do tempo fica de fora.
    assert linha["trechos"] == ("[00:10] Começa agora o Jornal da Manhã. [00:20] O setor Waldir Lins está sem energia "
                                "desde ontem. [00:30] Liga pra gente no [telefone].")
    assert "Calor de 38 graus" in tr.arquivo_completo("instagram", "1").read_text(encoding="utf-8")
    assert not list((tmp_path / "midia" / "tmp").iterdir())  # o áudio baixado é apagado
    assert tuple(con.execute("SELECT trechos, energia FROM transcricoes WHERE post_id = '2'").fetchone()) == (None, 0)
    # A IA viu os trechos da fala; a menção negativa virou alerta e entrou num agrupamento (fatos.py).
    assert len(pedidos) == 1 and mencoes.FORMATO_VIDEO_TRANSCRITO in pedidos[0]
    assert "Fala no vídeo: [00:10] Começa agora" in pedidos[0]
    assert "Waldir Lins" in vigia.ARQUIVO_ALERTAS.read_text(encoding="utf-8")
    assert con.execute("SELECT COUNT(*) FROM fato_mencao WHERE post_id = '1'").fetchone()[0] == 1
    # A ficha do painel e o relatório mostram os trechos.
    assert painel.post(con, "1")["falaNoVideo"] == linha["trechos"]
    assert relatorio.carregar(con).set_index("post_id").loc["1", "fala_no_video"] == linha["trechos"]


def test_sem_ia_a_fala_com_energia_fica_para_o_comando_mencoes(con, municipios, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIDIA_DIR", tmp_path / "midia")
    _radio(con, municipios)
    _na_fila(con, 1)
    Falsos({"1": ["A conta de luz subiu de novo."]}).rodar(con)
    [post] = mencoes.candidatos(con)  # a legenda não fala de energia; a fala, sim
    assert post["post_id"] == "1"
    assert "Fala no vídeo: [00:00] A conta de luz subiu de novo." in mencoes.descrever(post)


def test_endereco_vencido_falha_repetida_e_teto_do_dia(con, municipios, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIDIA_DIR", tmp_path / "midia")
    _radio(con, municipios)
    _na_fila(con, 1, 2, 3)
    con.execute("UPDATE transcricoes SET midia_expira = '2020-01-01T00:00:00+00:00' WHERE post_id = '2'")
    falsos = Falsos({}, erros={"1": ConnectionError("sem rede"), "3": tr.Expirado("HTTP 403")})
    falsos.rodar(con)
    estados = lambda: {l[0]: tuple(l)[1:] for l in con.execute(  # noqa: E731
        "SELECT post_id, estado, tentativas, midia_url IS NULL FROM transcricoes")}
    assert estados() == {"1": ("pendente", 1, 0), "2": ("expirado", 0, 1), "3": ("expirado", 0, 1)}
    for _ in range(tr.TENTATIVAS - 1):
        falsos.rodar(con)
    assert estados()["1"] == ("erro", tr.TENTATIVAS, 1)  # desiste, e o endereço sai do banco

    _na_fila(con, 4)
    monkeypatch.setattr(config, "TRANSCRICAO_MINUTOS_DIA", 1)
    con.execute("UPDATE transcricoes SET segundos = 120, feito_em = ? WHERE post_id = '1'",
                (datetime.now(timezone.utc).isoformat(),))
    assert Falsos({"4": ["Bom dia"]}).rodar(con)["teto"] == 1  # 2 min nas últimas 24 h: o 4 espera
    assert [f["post_id"] for f in tr.fila(con)] == ["4"]


def test_mascara_telefone_e_descarta_o_que_nao_e_fala():
    assert tr.mascarar("ligue (63) 3224-1000 ou 63 9 8888-7777") == "ligue [telefone] ou [telefone]"
    comum = "em 2026 a tarifa subiu 12,5% e a conta foi a R$ 1.234,56"
    assert tr.mascarar(comum) == comum

    def seg(texto, ns=0.1, lp=-0.3, cr=1.5):
        return SimpleNamespace(text=texto, no_speech_prob=ns, avg_logprob=lp, compression_ratio=cr)

    assert tr._valido(seg("Bom dia, Palmas!"))
    assert not tr._valido(seg("Legendas pela comunidade Amara.org"))
    assert not tr._valido(seg("♪ ♪"))
    assert not tr._valido(seg("hmm", ns=0.9, lp=-1.5))
    assert not tr._valido(seg("de novo de novo de novo de novo", cr=3.1))
