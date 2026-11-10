"""Páginas de fofoca (e de notícia local) em todos os municípios do TO, pela busca de posts do Instagram.

A busca de posts por palavra-chave mostra uns 24 posts por termo, com o autor, as curtidas e a data. Cada município
recebe o nome da cidade como termo (as cidades maiores, também "fofoca {c}" e "noticias {c}"). Só é lido o perfil
de quem tem post recente e curtido sobre a cidade: as contas largadas e os clones de 0 a 300 seguidores, que a busca
de contas trazia aos montes (ela mostra só 5 contas por termo, sem os seguidores), quase nunca aparecem ali. Ficam
de fora as contas já conhecidas, lojas e órgãos públicos, as da Energisa e as de cidades homônimas de fora do TO.
Os posts sobre energia que aparecem na busca já entram nas menções (busca_posts.ingerir).

Entram na fila também as contas que o radar já conhecia de nome (contas parecidas, buscas antigas) com cara de
página local e nome de cidade do TO. Depois de lidas, as contas vão para a IA. As que ela classificar como fofoca
do TO passam a ser vigiadas, e os portais e páginas de cidade com nota A/B também. Tudo é retomável: termos feitos
ficam em `buscas`, a fila em `tarefas`.
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config, db, enriquecimento, municipios as mun, perfis
from .influentes import RE_NOME_NEGOCIO, instituicao_ou_loja

ORIGEM = "busca_fofoca"
FONTE = "posts_fofoca"  # termos da busca de posts (a busca de contas antiga ficou em "pesquisa_nav")
RODADA = "fofoca-municipios"

# Por porte (municipios.porte): T1 acima de 40 mil habitantes, T2 de 10 a 40 mil, T3 abaixo de 10 mil.
TERMOS = {
    "T3": ["{c}"],
    "T2": ["{c}", "fofoca {c}"],
    "T1": ["{c}", "fofoca {c}", "noticias {c}"],
}
# Quem tem post assim na busca é lido: recente, curtido (o dobro, para quem não tem cara de página: só os
# influenciadores) e sobre uma cidade do TO.
MAX_DIAS = 180
MIN_CURTIDAS = 30
MIN_CURTIDAS_PESSOA = 1000

# Contas anônimas de escola e de bairro: das 77 lidas na 1ª rodada com isso no @, nenhuma tinha 300 seguidores e
# posts recentes.
RE_ANONIMA = re.compile(r"fofoc|fuxic|sigilo|crush|anonim|boato|fakenews|confiss|segredo")

# Municípios de todo o Brasil (IBGE), para reconhecer homônimos; mais lugares que não são municípios mas dominam a
# busca: as regiões do DF (Taguatinga, São Sebastião) e o estado do Paraná ("Paranã" sem acento).
ARQUIVO_BRASIL = Path(__file__).resolve().parent / "dados" / "municipios_brasil.csv"
OUTROS_LUGARES = ["Taguatinga", "São Sebastião", "Paraná"]
# Nomes de cidade do TO que também são palavras comuns: na legenda de um post, "peixe" quase nunca é a cidade.
PALAVRAS_COMUNS = ["Peixe", "Almas", "Talismã", "Novo Acordo", "Novo Jardim", "Barra do Ouro", "Mateiros",
                   "Combinado", "Centenário", "Fátima", "Aurora", "Natividade",
                   # "ananás" é abacaxi em várias línguas e trouxe RTL e Expressen; os outros são palavra ou nome.
                   "Ananás", "Arraias", "Lavandeira", "Pedro Afonso", "Bernardo Sayão", "Couto Magalhães"]
RE_CONECTIVO = re.compile(r" (?:de|do|da|dos|das|d) ")
RE_SINAL_TO = re.compile(r" (?:to|tocantins|063) ")

# Cara de página (não de pessoa nem de loja), no @ e no nome juntos e sem espaços.
RE_PAGINA = re.compile(r"fofoc|fuxic|alfinet|tuviu|babad|resenh|depre|milgrau|paparaz|bafaf|treta|polemic|mexeric|"
                       r"x9|linguarud|falatori|comentand|acontec|news|noticia|plantao|urgente|viral|memes|humor|"
                       r"zoeira|zuera|informa|emfoco|expoe|expond|denunc|reporter|diario|24h|24horas|online|agora|"
                       r"portal|alerta|ronda|policia|jornal|radio|folha|gazeta|revista|blog|foco|atualiza|"
                       r"enquantoisso|euamo")

# A cada quantas contas lidas a IA roda, para as páginas novas entrarem logo na vigilância.
CLASSIFICAR_A_CADA = 25


@dataclass(frozen=True)
class Homonimos:
    """`ambiguas`: municípios do TO (fora os T1) com o nome curto igual ao de um lugar de fora ("Taguatinga",
    "Paranã", "Lajeado"). `fora_compacto` e `fora_texto`: lugares de fora cujo nome contém o de uma cidade do TO
    ("valparaiso" tem "paraiso", "jiparana" tem "parana", "buritis" tem "buriti"), do maior para o menor."""
    ambiguas: frozenset
    fora_compacto: tuple
    fora_texto: tuple

    def limpar(self, username: str, nome: str | None) -> tuple[str, str]:
        """O @ compacto e o nome normalizado, sem os lugares de fora (trocados por "0")."""
        compacto = mun.compactar(username)
        for lugar in self.fora_compacto:
            if lugar in compacto:
                compacto = compacto.replace(lugar, "0")
        texto = f" {mun.normalizar(nome)} "
        for lugar in self.fora_texto:
            texto = texto.replace(f" {lugar} ", " 0 ")
        return compacto, texto.strip()


_HOMONIMOS: dict[tuple, Homonimos] = {}


def homonimos(municipios) -> Homonimos:
    chave = tuple(m["ibge_id"] for m in municipios)
    if chave not in _HOMONIMOS:
        _HOMONIMOS[chave] = _calcular_homonimos(municipios)
    return _HOMONIMOS[chave]


def _calcular_homonimos(municipios) -> Homonimos:
    with open(ARQUIVO_BRASIL, encoding="utf-8") as arquivo:
        nomes = {mun.normalizar(l["nome"]) for l in csv.DictReader(arquivo) if l["uf"] != "TO"}
    nomes |= {mun.normalizar(n) for n in OUTROS_LUGARES}
    formas = nomes | {RE_CONECTIVO.split(n)[0] for n in nomes}  # "valparaiso de goias" -> também "valparaiso"
    nomes |= {mun.normalizar(n) for n in PALAVRAS_COMUNS}  # só para exigir o sinal de TO, não para limpar o texto
    curtos = {m["ibge_id"]: mun.normalizar(mun.nome_curto(m["nome"])) for m in municipios}
    ambiguas = frozenset(m["ibge_id"] for m in municipios
                         if mun.porte(m["populacao"]) != "T1" and curtos[m["ibge_id"]] in nomes)
    do_to = set(curtos.values())
    compactos_to = {c.replace(" ", "") for c in do_to if len(c.replace(" ", "")) >= 4}
    fora_compacto = {f.replace(" ", "") for f in formas if f not in do_to
                     and any(c in f.replace(" ", "") and c != f.replace(" ", "") for c in compactos_to)}
    fora_texto = {f for f in formas if f not in do_to and any(f" {c} " in f" {f} " for c in do_to)}
    por_tamanho = lambda xs: tuple(sorted(xs, key=lambda x: (-len(x), x)))  # noqa: E731
    return Homonimos(ambiguas, por_tamanho(fora_compacto), por_tamanho(fora_texto))


def cidade_do_termo(municipio: dict, ambigua: bool = False) -> str:
    """'Colinas do Tocantins' -> 'colinas to'; 'Gurupi' -> 'gurupi'. Sem o "to", "lajeado news" traz o Lajeado
    do RS: as cidades homônimas também levam o "to"."""
    curto = mun.nome_curto(municipio["nome"])
    return mun.normalizar(curto) + ("" if curto == municipio["nome"] and not ambigua else " to")


def termos(municipio: dict, hom: Homonimos | None = None) -> list[str]:
    cidade = cidade_do_termo(municipio, bool(hom and municipio["ibge_id"] in hom.ambiguas))
    return [t.format(c=cidade) for t in TERMOS[mun.porte(municipio["populacao"])]]


def _negocio(username: str, nome: str | None) -> bool:
    return bool(RE_NOME_NEGOCIO.search(mun.compactar(f"{username} {nome or ''}"))
                or instituicao_ou_loja({"nome": nome}))


def tem_sinal_do_to(username: str, texto: str | None, cidades: set[int], municipios) -> bool:
    """"TO", "Tocantins" ou o DDD 063 no @ ou no texto, ou o @ com a cidade seguida de "to" ("colinas_tonews")."""
    compacto = mun.compactar(username)
    if "tocantins" in compacto or "063" in compacto:
        return True
    if RE_SINAL_TO.search(f" {mun.normalizar(username)} {mun.normalizar(texto)} "):
        return True
    curtos = [mun.compactar(mun.nome_curto(m["nome"])) for m in municipios if m["ibge_id"] in cidades]
    return any(f"{c}to" in compacto or f"{c}doto" in compacto for c in curtos)


def cidades_citadas(username: str, nome: str | None, municipios, alvo: dict | None = None,
                    sinal: str | None = None) -> set[int]:
    """Municípios do TO no @ ou no nome. O município pesquisado vale mesmo com nome curto ('peixe', 'almas').
    Nomes de lugares de fora não contam ("valparaiso" não é Paraíso), e uma cidade homônima (Taguatinga, Lajeado)
    só conta com sinal de TO no @, no nome ou em `sinal` (a bio, depois da leitura)."""
    hom = homonimos(municipios)
    compacto, texto = hom.limpar(username, nome)
    achados = mun.no_username(compacto, municipios) | mun.no_texto(texto, municipios)
    if alvo and any(len(v) >= 4 and " " not in v and v in compacto for v in alvo["variantes"]):
        achados.add(alvo["ibge_id"])
    if achados & hom.ambiguas and not tem_sinal_do_to(username, f"{nome or ''} {sinal or ''}", achados, municipios):
        achados -= hom.ambiguas
    return achados


def anonima(username: str, nome: str | None) -> bool:
    return bool(RE_ANONIMA.search(mun.compactar(username))) or not (nome or "").strip()


def _engajamento(item: dict) -> int:
    curtidas = item.get("likesCount")
    if isinstance(curtidas, int) and curtidas >= 0:
        return curtidas
    return 10 * (item.get("commentsCount") or 0)  # curtidas ocultas: os comentários dão a ordem de grandeza


def candidatas_dos_posts(itens: list[dict], alvo: dict, municipios, agora: datetime) -> dict[str, dict]:
    """Autores dos posts da busca que valem a leitura do perfil, com os municípios que citam (no @, no nome ou na
    legenda e no local dos posts, com a regra dos homônimos)."""
    autores: dict[str, dict] = {}
    for item in itens:
        dono = item.get("ownerUsername")
        if not dono:
            continue
        autor = autores.setdefault(dono, {"nome": item.get("ownerFullName"), "privada": item.get("ownerIsPrivate"),
                                          "curtidas": 0, "dias": float("inf"), "cidades": set(), "tocantins": False})
        autor["curtidas"] = max(autor["curtidas"], _engajamento(item))
        data = perfis.ler_data(item.get("timestamp"))
        if data:
            autor["dias"] = min(autor["dias"], (agora - data).total_seconds() / 86400)
        texto = f"{item.get('caption') or ''} {item.get('locationName') or ''}"
        autor["cidades"] |= cidades_citadas("", texto, municipios)
        autor["tocantins"] |= "tocantins" in mun.normalizar(texto).split()
    escolhidas = {}
    for dono, autor in autores.items():
        if (autor["privada"] or "energisa" in dono or _negocio(dono, autor["nome"]) or autor["dias"] > MAX_DIAS
                or not (autor["nome"] or "").strip()):
            continue
        pagina = bool(RE_PAGINA.search(mun.compactar(f"{dono} {autor['nome'] or ''}")))
        if autor["curtidas"] < (MIN_CURTIDAS if pagina else MIN_CURTIDAS_PESSOA):
            continue
        cidades = cidades_citadas(dono, autor["nome"], municipios, alvo) | autor["cidades"]
        if cidades or autor["tocantins"]:
            escolhidas[dono] = {**autor, "cidades": cidades}
    return escolhidas


def _conhecida(con, username: str) -> bool:
    """Já lida e já resolvida (classificada pela IA ou descartada): não precisa entrar na fila."""
    return con.execute(
        """SELECT 1 FROM contas c WHERE c.rede = 'instagram' AND c.username = ? AND c.completo = 1
             AND (c.descartada IS NOT NULL OR EXISTS (SELECT 1 FROM classificacoes k WHERE k.rede = c.rede
                                                      AND k.username = c.username))""", (username,)).fetchone()


def _enfileirar(con, username: str) -> bool:
    cursor = con.execute("INSERT OR IGNORE INTO tarefas (rodada, username, estado) VALUES (?, ?, 'pendente')",
                         (RODADA, username))
    return cursor.rowcount > 0


def _ja_lida(con, username: str) -> bool:
    linha = con.execute("SELECT completo FROM contas WHERE rede = 'instagram' AND username = ?", (username,)).fetchone()
    return bool(linha and linha[0])


def enfileirar_conhecidas(con, municipios) -> int:
    """Contas que o radar já tem (só de nome, ou lidas e nunca classificadas) com cara de página local e nome de
    cidade do TO. As já lidas entram como feitas: vão direto para a IA, sem leitura."""
    nomes = {}
    for username, nome in con.execute(
            """SELECT r.relacionada, r.nome FROM relacionadas r
               LEFT JOIN contas c ON c.rede = r.rede AND c.username = r.relacionada
               WHERE r.rede = 'instagram' AND c.username IS NULL"""):
        nomes[username] = nomes.get(username) or nome
    for username, nome in con.execute(
            """SELECT username, nome FROM contas c WHERE rede = 'instagram' AND descartada IS NULL
                 AND COALESCE(privada, 0) = 0 AND NOT EXISTS
                 (SELECT 1 FROM classificacoes k WHERE k.rede = c.rede AND k.username = c.username)"""):
        nomes[username] = nomes.get(username) or nome
    novas = 0
    for username, nome in nomes.items():
        if (RE_PAGINA.search(mun.compactar(f"{username} {nome or ''}")) and not anonima(username, nome)
                and not _negocio(username, nome) and cidades_citadas(username, nome, municipios)):
            novas += _enfileirar(con, username)
    con.commit()
    return novas


def _ler_fila(con, navegador, municipios, limite: int) -> int:
    """Lê os perfis pendentes da fila (até `limite` leituras) e aplica o pré-filtro de sempre."""
    lidas = 0
    pendentes = [l[0] for l in con.execute(
        "SELECT username FROM tarefas WHERE rodada = ? AND estado = 'pendente' ORDER BY rowid", (RODADA,))]
    a_ler = []
    for username in pendentes:
        # Enfileirada antes da regra das anônimas (aqui só pelo @: a conhecida de nome pode não ter linha em contas).
        if not _ja_lida(con, username) and RE_ANONIMA.search(mun.compactar(username)):
            con.execute("UPDATE contas SET descartada = 'anonima' WHERE rede = 'instagram' AND username = ?",
                        (username,))
        elif not _ja_lida(con, username):
            a_ler.append(username)
            continue
        con.execute("UPDATE tarefas SET estado = 'feita', terminada_em = ? WHERE rodada = ? AND username = ?",
                    (db.agora(), RODADA, username))
    con.commit()
    while a_ler and lidas < limite:
        lote = a_ler[:min(navegador.lote_maximo, limite - lidas)]
        a_ler = a_ler[len(lote):]
        _, itens = navegador.rodar(config.ATOR_PERFIL, {"usernames": lote}, "perfil")
        achadas = set()
        for item in itens:
            username = perfis.ingerir(con, item, ORIGEM, municipios, 1)
            if username:
                achadas.add(username)
        for username in lote:
            if username in achadas:
                conta = dict(con.execute("SELECT * FROM contas WHERE rede = 'instagram' AND username = ?",
                                         (username,)).fetchone())
                motivo = enriquecimento.motivo_descarte(conta)
                if motivo:
                    con.execute("UPDATE contas SET descartada = ? WHERE rede = 'instagram' AND username = ?",
                                (motivo, username))
            else:
                db.upsert_conta(con, {"username": username, "origem": ORIGEM, "profundidade": 1})
                con.execute("UPDATE contas SET descartada = 'nao_encontrada' WHERE rede = 'instagram' "
                            "AND username = ?", (username,))
            con.execute("UPDATE tarefas SET estado = 'feita', terminada_em = ? WHERE rodada = ? AND username = ?",
                        (db.agora(), RODADA, username))
        con.commit()
        lidas += len(lote)
    return lidas


def descartar_de_fora(con, municipios) -> int:
    """Contas da fila já lidas que, pela regra dos homônimos (com a bio como sinal de TO), não são de cidade do TO:
    ficam descartadas como `fora_do_to`, sem ir à IA. Ficam as que a IA já ligou a um município do TO e as que a
    busca ligou a uma cidade sem homônimo ("fofocapeixe" na busca de Peixe)."""
    hom = homonimos(municipios)
    linhas = con.execute(
        """SELECT c.username, c.nome, c.bio FROM tarefas t JOIN contas c ON c.rede = 'instagram'
             AND c.username = t.username
           WHERE t.rodada = ? AND c.completo = 1 AND c.descartada IS NULL""", (RODADA,)).fetchall()
    fora = 0
    for username, nome, bio in linhas:
        ligadas = {(l[0], l[1]) for l in con.execute(
            "SELECT ibge_id, fonte FROM conta_municipio WHERE rede = 'instagram' AND username = ?", (username,))}
        if any(fonte == "ia" or (fonte == "busca" and i not in hom.ambiguas) for i, fonte in ligadas):
            continue
        if (not cidades_citadas(username, nome, municipios, sinal=bio)
                and not tem_sinal_do_to(username, f"{nome or ''} {bio or ''}", set(), municipios)):
            con.execute("UPDATE contas SET descartada = 'fora_do_to' WHERE rede = 'instagram' AND username = ?",
                        (username,))
            fora += 1
    con.commit()
    return fora


def a_classificar(con) -> list[dict]:
    """Contas da fila já lidas, públicas, não descartadas e ainda sem classificação da IA."""
    return [dict(l) for l in con.execute(
        """SELECT c.* FROM tarefas t JOIN contas c ON c.rede = 'instagram' AND c.username = t.username
           WHERE t.rodada = ? AND t.estado = 'feita' AND c.completo = 1 AND c.descartada IS NULL
             AND COALESCE(c.privada, 0) = 0
             AND NOT EXISTS (SELECT 1 FROM classificacoes k WHERE k.rede = c.rede AND k.username = c.username)
           ORDER BY c.seguidores DESC""", (RODADA,))]


def rodada(con, navegador, orcamento: int, aviso=print, classificar=None) -> dict:
    """Busca em todos os municípios, do mais ao menos populoso, com até `orcamento` leituras. Depois das buscas de
    cada município, lê as contas novas da fila. `classificar(contas)` roda a IA de tempos em tempos."""
    municipios = mun.carregar(con)
    hom = homonimos(municipios)
    inicio = navegador.leituras
    usadas = lambda: navegador.leituras - inicio  # noqa: E731
    resumo = Counter(conhecidas=enfileirar_conhecidas(con, municipios),
                     fora_do_to=descartar_de_fora(con, municipios))
    feitas = db.termos_executados(con, FONTE)

    def ia(minimo: int) -> None:
        resumo["fora_do_to"] += descartar_de_fora(con, municipios)
        contas = a_classificar(con)
        if classificar and len(contas) >= minimo:
            classificar(contas)
            resumo["classificadas"] += len(contas)

    from . import busca_posts

    for municipio in municipios:
        pendentes = [t for t in termos(municipio, hom) if t not in feitas]
        for termo in pendentes:
            if usadas() >= orcamento:
                break
            itens = navegador.buscar_posts(termo)
            agora = datetime.now(timezone.utc)
            resumo["posts_sobre_energia"] += busca_posts.ingerir(con, termo, itens, municipios, agora)["novos"]
            for username, autor in candidatas_dos_posts(itens, municipio, municipios, agora).items():
                if _conhecida(con, username):
                    continue
                lida = _ja_lida(con, username)  # já lida: entra na fila só para a IA, se ainda não passou por ela
                if not lida:
                    db.upsert_conta(con, {"username": username, "nome": autor["nome"], "origem": ORIGEM,
                                          "profundidade": 1})
                if municipio["ibge_id"] in autor["cidades"]:
                    db.vincular_municipio(con, username, municipio["ibge_id"], "busca")
                if _enfileirar(con, username) and not lida:
                    resumo["novas"] += 1
            db.registrar_busca(con, FONTE, termo, municipio["ibge_id"], None, len(itens))
            con.commit()
            feitas.add(termo)
            resumo["pesquisas"] += 1
        if pendentes:
            resumo["municipios"] += 1
            lidas = _ler_fila(con, navegador, municipios, max(orcamento - usadas(), 0))
            resumo["perfis"] += lidas
            if lidas:
                ia(CLASSIFICAR_A_CADA)
            aviso(f"{municipio['nome']}: {resumo['pesquisas']} buscas e {resumo['perfis']} perfis lidos até agora "
                  f"({usadas()} leituras).")
        if usadas() >= orcamento:
            break
    resumo["perfis"] += _ler_fila(con, navegador, municipios, max(orcamento - usadas(), 0))
    ia(1)
    resumo.update(situacao(con))
    resumo["leituras"] = usadas()
    return dict(resumo)


def cobertura(con) -> dict:
    """Páginas de fofoca do TO (pela IA) e em quantos municípios elas estão."""
    filtro = """FROM classificacoes k JOIN contas c ON c.rede = k.rede AND c.username = k.username
                JOIN conta_municipio cm ON cm.rede = k.rede AND cm.username = k.username AND cm.fonte = 'ia'
                WHERE k.tipo = 'perfil_fofoca' AND c.descartada IS NULL AND COALESCE(c.privada, 0) = 0"""
    municipios, paginas = con.execute(f"SELECT COUNT(DISTINCT cm.ibge_id), COUNT(DISTINCT k.username) {filtro}"
                                      ).fetchone()
    return {"paginas_de_fofoca": paginas, "municipios_com_fofoca": municipios}


def situacao(con) -> dict:
    """Retrato da busca, sem nenhuma leitura."""
    municipios = mun.carregar(con)
    feitas = db.termos_executados(con, FONTE)
    hom = homonimos(municipios)
    todos = [t for m in municipios for t in termos(m, hom)]
    fila = dict(con.execute("SELECT estado, COUNT(*) FROM tarefas WHERE rodada = ? GROUP BY estado",
                            (RODADA,)).fetchall())
    return {"buscas_feitas": sum(t in feitas for t in todos), "buscas_faltando": sum(t not in feitas for t in todos),
            "fila_pendente": fila.get("pendente", 0), "fila_lida": fila.get("feita", 0),
            "a_classificar": len(a_classificar(con)), **cobertura(con)}
