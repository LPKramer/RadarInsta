import pytest

from radar import config, navegador as nav, vigia


@pytest.fixture
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NAVEGADOR_DIR", tmp_path)
    for nome in ("perfil", "perfil-conta2", "perfil-conta6"):
        (tmp_path / nome).mkdir()
    monkeypatch.setattr(nav, "tem_sessao", lambda _pasta: True)
    return tmp_path


def test_desativar_e_ativar(pasta):
    assert nav.desativada("conta6") is None
    nav.desativar("conta6", "suspensa pelo Instagram")
    assert nav.desativada("conta6").startswith("suspensa pelo Instagram (desde ")
    assert nav.contas_configuradas(pasta) == ["principal", "conta2", "conta6"]  # continua na lista, marcada
    assert vigia.contas_novas(set()) == ["principal", "conta2"]  # mas não entra na vigilância
    assert nav.ativar("conta6") and not nav.ativar("conta6")
    assert vigia.contas_novas(set()) == ["principal", "conta2", "conta6"]


def test_conta_desativada_nao_abre(pasta):
    nav.desativar("conta6", "desativada à mão")
    with pytest.raises(nav.ContaDesativada, match="navegador-ativar conta6"):
        with nav.abrir(conta="conta6"):
            pass
    resultado = vigia.trabalhador("conta6", "2099-01-01T00:00:00+00:00", str(pasta / "radar.db"), False, False)
    assert resultado["sem_login"] and "desativada" in resultado["parou"]  # não conta como bloqueio do Instagram
    assert not vigia._falha_passageira(resultado)  # nem é tentada de novo


class PaginaFalsa:
    def __init__(self, visivel, pasta):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *erro):
        return False

    def logado(self):
        return True


def test_suspensa_pelo_instagram_fica_desativada(pasta, monkeypatch):
    monkeypatch.setattr(nav, "PaginaInstagram", PaginaFalsa)
    with pytest.raises(nav.ColetaInterrompida):
        with nav.abrir(conta="conta6"):
            raise nav.ColetaInterrompida("O Instagram levou o navegador para "
                                         "https://www.instagram.com/accounts/suspended/?next=%2Fportal%2F.")
    assert nav.desativada("conta6").startswith("suspensa pelo Instagram")

    with pytest.raises(nav.ColetaInterrompida):  # pedido de verificação não desativa: passa em algumas horas
        with nav.abrir(conta="conta2"):
            raise nav.ColetaInterrompida("O Instagram levou o navegador para https://www.instagram.com/challenge/.")
    assert nav.desativada("conta2") is None


def test_remover_apaga_o_perfil_e_a_marca(pasta):
    nav.desativar("conta6", "suspensa pelo Instagram")
    (pasta / "leituras-conta6.json").write_text("[]")
    assert len(nav.remover("conta6")) == 2
    assert not (pasta / "perfil-conta6").exists() and nav.desativada("conta6") is None
    assert (pasta / "leituras-conta6.json").exists()  # o teto continua valendo se a conta voltar com o mesmo nome
    assert nav.remover("conta6") == []


def test_nao_remove_conta_em_uso(pasta, monkeypatch):
    monkeypatch.setattr(nav, "tem_sessao", lambda _pasta: None)  # o Edge está com o perfil aberto
    with pytest.raises(RuntimeError, match="aberta em outra janela"):
        nav.remover("conta2")
    assert (pasta / "perfil-conta2").exists()
