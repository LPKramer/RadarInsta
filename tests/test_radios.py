import re

from radar import config, db, monitoramento, radios, vigia

from conftest import deepseek_falso, resposta_deepseek


def _conta(con, username, nome="", bio="", seguidores=1000):
    db.upsert_conta(con, {"username": username, "nome": nome, "bio": bio, "seguidores": seguidores, "origem": "t"})
    con.commit()


def test_candidatas_pelo_nome_bio_ou_tipo(con):
    _conta(con, "terrafm965", "Terra FM 96,5")
    _conta(con, "joao.locutor", "João", "Locutor da Rádio Sucesso")
    _conta(con, "portaltexto", "Portal Texto", "Notícias de Palmas")
    _conta(con, "energisa_radio", "Rádio Energisa")  # a própria Energisa fica fora (regra do usuário)
    assert sorted(c["username"] for c in radios.candidatas(con)) == ["joao.locutor", "terrafm965"]


class Busca:
    """Coletor falso: a busca devolve contas fixas; o perfil vem no formato da Apify."""

    def __init__(self):
        self.lidos = []

    def pesquisar(self, termo):
        return [{"username": "cbntocantins", "nome": "CBN Tocantins 101,9"},
                {"username": "lojadoze", "nome": "Loja do Zé"}]

    def rodar(self, ator, entrada, rotulo):
        self.lidos += entrada["usernames"]
        return "nav", [{"username": u, "fullName": "CBN Tocantins", "biography": "Rádio CBN 101,9 FM Palmas",
                        "followersCount": 20000, "latestPosts": []} for u in entrada["usernames"]]


def test_descobrir_le_so_as_novas_com_cara_de_radio(con):
    coletor = Busca()
    resumo = radios.descobrir(con, coletor, orcamento=3, aviso=lambda _: None)
    assert resumo["buscas"] == 2 and coletor.lidos == ["cbntocantins"]  # a loja fica de fora; orçamento de 3
    assert con.execute("SELECT nome FROM contas WHERE username = 'cbntocantins'").fetchone()[0] == "CBN Tocantins"


class BuscaCidade(Busca):
    """Na busca "rádio Xambioá": a rádio da cidade, uma rádio de outro estado e uma página que não é rádio."""

    def __init__(self):
        super().__init__()
        self.termos = []

    def pesquisar(self, termo):
        self.termos.append(termo)
        if termo != "rádio Xambioá":
            return []
        return [{"username": "radioxambioafm", "nome": "Rádio Xambioá FM 89,1"},
                {"username": "radiofmsp", "nome": "Rádio FM São Paulo"},
                {"username": "xambioanoticias", "nome": "Xambioá Notícias"}]


def test_busca_por_cidade_le_so_quem_cita_a_cidade_e_continua_de_onde_parou(con):
    lista = radios.termos(con)
    assert ("som alvorada", None) in lista and ("rádio Xambioá", "Xambioá") in lista  # rede e município do MCom
    con.executemany("INSERT INTO buscas (fonte, termo, executada_em, n_resultados) VALUES (?, ?, 'x', 0)",
                    [(radios.FONTE_BUSCA, t) for t, _ in lista if t != "rádio Xambioá"])
    coletor = BuscaCidade()
    # Sem orçamento para o perfil, o termo não conta como feito: volta na próxima vez.
    assert radios.descobrir(con, coletor, orcamento=1, aviso=lambda _: None)["sem_orcamento"] == 1
    resumo = radios.descobrir(con, coletor, orcamento=10, aviso=lambda _: None)
    assert coletor.termos == ["rádio Xambioá", "rádio Xambioá"] and coletor.lidos == ["radioxambioafm"]
    assert resumo["perfis"] == 1
    assert radios.descobrir(con, coletor, orcamento=10, aviso=lambda _: None)["buscas"] == 0  # nada a repetir


def test_classificar_propor_e_aprovar(con, tmp_path):
    _conta(con, "terrafm965", "Terra FM 96,5", "Araguaína", 9000)
    _conta(con, "difusorafm93", "Difusora FM 93", "Imperatriz - MA", 5000)
    _conta(con, "portalx", "Portal X", "FM? não, só texto", 100)
    respostas = {
        "terrafm965": {"categoria": "emissora", "cidade": "Araguaína", "uf": "TO", "confianca": 0.6,
                       "publica_videos": True, "evidencias": "96,5 FM Araguaína"},
        "difusorafm93": {"categoria": "emissora", "cidade": "Imperatriz", "uf": "TO", "confianca": 0.9,
                         "publica_videos": True, "evidencias": "a IA errou a UF"},
        "portalx": {"categoria": "nao", "cidade": None, "uf": None, "confianca": 0.2, "evidencias": ""},
    }

    def responder(params):
        dono = re.search(r"Conta: @(\S+)", params["messages"][1]["content"]).group(1)
        return resposta_deepseek(respostas[dono])

    cliente, _ = deepseek_falso(responder)
    resumo = radios.classificar(con, cliente, radios.candidatas(con), aviso=lambda _: None)
    assert resumo == {"emissora": 2, "nao": 1}
    linhas = {l[0]: tuple(l)[1:] for l in con.execute("SELECT username, uf, do_to FROM radios")}
    assert linhas["terrafm965"] == ("TO", 1)  # para a rádio basta a cidade do TO, mesmo com confiança 0,6
    assert linhas["difusorafm93"] == ("MA", 0)  # o IBGE corrige: Imperatriz é do Maranhão
    assert radios.nao_classificadas(con, radios.candidatas(con)) == []

    caminho, n = radios.propor(con, tmp_path / "radios.xlsx")
    assert n == 2  # a classificada como "nao" não entra na proposta
    import openpyxl

    aba = openpyxl.load_workbook(caminho)["Rádios"]
    linhas = list(aba.iter_rows(values_only=True))
    assert linhas[1][1] == "terrafm965"  # as do TO primeiro
    aba.cell(row=2, column=1, value="sim")
    aba.parent.save(caminho)
    assert radios.aprovadas_na_planilha(caminho) == ["terrafm965"]
    assert radios.aprovar(con, ["@terrafm965", "naoexiste"]) == 1
    assert "terrafm965" in {c["username"] for c in monitoramento.monitoradas(con)}
    assert vigia.intervalos(con)["terrafm965"] == config.VIGIA_HORAS["media"]
