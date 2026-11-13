"""Rádios ao vivo (aovivo.py): a gravação em blocos na janela dos jornais e o caminho até a menção. O ffmpeg é falso."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from radar import aovivo, config, mencoes, transcricao as tr, vigia

from conftest import analise, deepseek_falso, resposta_deepseek

SEIS = datetime(2026, 11, 12, 6, 0, tzinfo=aovivo.FUSO).astimezone(timezone.utc)


def emissora(slug="radiox", stream="https://s/x"):
    return {"slug": slug, "nome": "Rádio X", "cidade": "Palmas", "site": "", "stream": stream, "inicio": "06:00",
            "fim": "09:00", "ativo": "sim"}


class Processo:
    """O ffmpeg falso: o teste diz quando terminou (returncode) e escreve o arquivo gravado."""
    todos = []

    def __init__(self, cmd, **_):
        self.cmd, self.returncode, self.destino = cmd, None, Path(cmd[-1])
        Processo.todos.append(self)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, tempo=None):
        return self.returncode

    def segundos(self):
        return int(self.cmd[self.cmd.index("-t") + 1])


def test_grava_em_blocos_na_janela_e_poe_na_fila(con, tmp_path):
    Processo.todos = []
    g = aovivo.Gravador(con, aviso=lambda _: None, abrir=Processo, pasta=tmp_path,
                        lista=[emissora(), emissora("mudo", "https://s/mudo")])
    assert g.passo(SEIS - timedelta(minutes=5))["gravando"] == 0  # antes das 6h
    assert g.passo(SEIS)["gravando"] == 2
    x, mudo = Processo.todos
    assert x.segundos() == 1800 and "libopus" in x.cmd
    x.destino.write_bytes(b"0" * 20_000)
    x.returncode, mudo.returncode = 0, 1  # a X gravou o bloco; a outra caiu na hora, sem arquivo
    resumo = g.passo(SEIS + timedelta(minutes=30))
    assert (resumo["blocos"], resumo["sem_sinal"], resumo["gravando"]) == (1, 1, 1)  # a X segue; a muda espera
    estado, url, legenda = con.execute(
        "SELECT t.estado, t.midia_url, p.legenda FROM transcricoes t JOIN posts p ON p.rede = t.rede "
        "AND p.post_id = t.post_id WHERE t.rede = 'radio'").fetchone()
    assert estado == "pendente" and url.startswith("file:") and "das 06:00 às 06:30" in legenda
    assert g.passo(SEIS + timedelta(minutes=36))["gravando"] == 1  # 5 min depois, a muda tenta de novo
    # O último bloco termina com a janela, às 9h; depois, nada.
    g2 = aovivo.Gravador(con, aviso=lambda _: None, abrir=Processo, pasta=tmp_path, lista=[emissora("tarde")])
    g2.passo(SEIS + timedelta(hours=2, minutes=45))
    assert Processo.todos[-1].segundos() == 900
    assert aovivo.fim_da_janela(emissora(), SEIS + timedelta(hours=3)) is None


def test_bloco_com_energia_vira_mencao_e_alerta(con, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIDIA_DIR", tmp_path / "midia")
    arquivo = tmp_path / "bloco.ogg"
    arquivo.write_bytes(b"0" * 20_000)
    aovivo.registrar(con, emissora(), arquivo, SEIS, SEIS + timedelta(minutes=30))
    convertidos, pedidos = [], []

    def converter(caminho, max_segundos=None):
        convertidos.append(max_segundos)
        return [0.0] * (tr.TAXA * 60)

    def transcritor(audio):
        return [tr.Trecho(0, 9, "Bom dia, Palmas."),
                tr.Trecho(10, 19, "Ouvinte avisa que a quadra 405 Sul está sem energia desde as cinco da manhã.")]

    def responder(params):
        pedidos.append(params["messages"][1]["content"])
        return resposta_deepseek(analise(resumo="Quadra 405 Sul, em Palmas, sem energia desde a madrugada."))

    cliente, _ = deepseek_falso(responder)
    resumo = tr.rodar(con, cliente, transcritor, aviso=lambda _: None,
                      baixar=lambda *_: pytest.fail("o bloco ao vivo já está no disco"), converter=converter)
    assert (resumo["feita"], resumo["mencoes"]) == (1, 1) and convertidos == [tr.MAX_SEGUNDOS_AO_VIVO]
    assert not arquivo.exists()  # o áudio sai do disco depois de transcrito
    assert mencoes.FORMATO_AO_VIVO in pedidos[0] and "Rádio: Rádio X" in pedidos[0] and "405 Sul" in pedidos[0]
    assert "rádio ao vivo radiox" in vigia.ARQUIVO_ALERTAS.read_text(encoding="utf-8")
