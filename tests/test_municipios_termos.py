import re

from radar import municipios as mun, termos as tm


def nome_para_id(municipios, nome):
    return next(m["ibge_id"] for m in municipios if m["nome"] == nome)


def test_csv_tem_todos_os_municipios(municipios):
    assert len(municipios) == 139
    portes = {p: sum(m["porte"] == p for m in municipios) for p in ("T1", "T2", "T3")}
    assert portes == {"T1": 5, "T2": 23, "T3": 111}
    assert municipios[0]["nome"] == "Palmas"  # ordenado por população


def test_normalizacao_e_nome_curto():
    assert mun.normalizar("  Paraíso do Tocantins ") == "paraiso do tocantins"
    assert mun.nome_curto("Paraíso do Tocantins") == "Paraíso"
    assert mun.nome_curto("Palmas") == "Palmas"
    assert "paraisoto" in mun.variantes("Paraíso do Tocantins")
    assert "pmw" in mun.variantes("Palmas")


def test_casar_nome_tolera_variacoes(municipios):
    paraiso = nome_para_id(municipios, "Paraíso do Tocantins")
    for escrito in ["Paraíso", "paraiso do tocantins", "Paraíso - TO", "Paraiso/TO", "Paraíso do TO"]:
        assert mun.casar_nome(escrito, municipios) == paraiso, escrito
    assert mun.casar_nome("Araguaina", municipios) == nome_para_id(municipios, "Araguaína")
    assert mun.casar_nome("São Paulo", municipios) is None
    assert mun.casar_nome("", municipios) is None


def test_municipio_na_bio_e_no_username(municipios):
    gurupi = nome_para_id(municipios, "Gurupi")
    assert gurupi in mun.no_texto("Notícias de Gurupi e região sul do TO", municipios)
    assert gurupi in mun.no_username("gurupinoticias", municipios)
    assert mun.no_username("abc_news", municipios) == set()


def test_termos_por_porte(municipios):
    palmas = next(m for m in municipios if m["nome"] == "Palmas")
    pequeno = next(m for m in municipios if m["porte"] == "T3")
    assert len(tm.termos_do_municipio(palmas)) == 12
    assert len(tm.termos_do_municipio(pequeno)) == 3
    assert "palmasto" in tm.termos_do_municipio(palmas)


def test_termos_sem_caractere_especial_nem_repeticao(municipios):
    termos = tm.gerar(municipios)
    textos = [t.termo for t in termos]
    assert len(textos) == len(set(textos))
    assert all(re.fullmatch(r"[a-z0-9 ]+", t) for t in textos)
    assert any(t.ibge_id is None for t in termos)  # termos estaduais
    assert not any(t.ibge_id is None for t in tm.gerar(municipios[:1], incluir_estado=False))
