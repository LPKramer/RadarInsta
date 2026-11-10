"""As 1.000 páginas e perfis do Tocantins com mais seguidores, que completam a base monitorada.

A descoberta anda em rodadas pelo navegador, com um orçamento de leituras, e retoma de onde parou:
1. busca do Instagram com termos ("influencer palmas", "cantor tocantins"...) e nomes de município;
2. página das maiores contas do TO já aceitas que ainda não têm sugestões guardadas: as contas parecidas que o
   Instagram sugere costumam ser outras contas do TO (toda visita a um perfil também traz essas sugestões);
3. leitura do perfil dos candidatos mais promissores (sinal de TO no @ ou no nome, ligação com contas do TO).
O critério é a contagem de seguidores. Entram pessoas, criadores e páginas (notícia, fofoca, entretenimento, cidade);
ficam de fora empresas e órgãos públicos. Quem é privado, fica abaixo do corte (a 1.000ª conta), é instituição ou
loja, ou não mostra ligação com o TO sai por regra, sem gastar IA. Os finalistas passam pelo `classificar`; `montar_lista` grava as 1.000 que entram no
monitoramento.
"""
import re
from collections import Counter

from . import config, db, monitoramento, municipios as mun, perfis
from .enriquecimento import ORIGEM_INFLUENTES

LISTA = "mais_seguidas_to"
LISTA_INFLUENCIADORES = "influenciadores_to"
TAMANHO_LISTA = 1000
TAMANHO_INFLUENCIADORES = 100
CORTE_INICIAL = 10_000
ORIGEM = ORIGEM_INFLUENTES
# Tipos da IA que entram na lista: todos menos órgão público e irrelevante (comércio, serviço, outro estado).
TIPOS_LISTA = ["influenciador_local", "jornalista_radialista", "politico", "portal_noticias", "perfil_fofoca",
               "policial_plantao", "pagina_cidade_comunidade"]
# A partir desta data o prompt da IA reconhece criadores, artistas e atletas. Contas do TO acima do corte que o
# prompt antigo classificou como irrelevantes voltam uma vez para a IA.
PROMPT_PESSOAS_DESDE = "2026-11-04"
# Parte do orçamento de cada rodada que pode ir para a busca e, somada, para as contas parecidas.
FATIA_BUSCA = 0.25
FATIA_SEMELHANTES = 0.45
PRIORIDADE_MINIMA = 2

TERMOS = [
    "tocantins", "tocantinense", "influencer tocantins", "influenciadora tocantins", "blogueira tocantins",
    "humorista tocantins", "cantor tocantins", "cantora tocantins", "atleta tocantins", "apresentador tocantins",
    "jornalista tocantins", "deputado tocantins", "vereador palmas", "influencer palmas", "blogueira palmas",
    "humor palmas", "memes tocantins", "fofoca palmas", "trends palmas", "palmas to",
    "influencer araguaina", "blogueira araguaina", "influencer gurupi", "influencer porto nacional",
    "influencer paraiso do tocantins",
]
RE_TOCANTINS = re.compile(r"\btocantin(s|ense|enses)\b")
RE_INSTITUICAO = re.compile(r"\b(prefeitura|camara municipal|camara de vereadores|secretaria|governo|tribunal|"
                            r"policia|bombeiros|detran|assembleia|ministerio|defensoria|universidade|faculdade|"
                            r"instituto federal|ifto|uft|sebrae|senac|senai|sesc|igreja|paroquia|diocese)\b")
# Mais estreito que o filtro de comércio do enriquecimento: "moda", "beleza" e "cupom" são comuns em bio de
# influenciador. A palavra final é da IA; aqui só sai o que é claramente loja ou serviço.
RE_LOJA = re.compile(r"\b(delivery|pedidos?|encomendas?|atacado|varejo|cardapio|agende|agendamento|orcamento|"
                     r"clinica|consultorio|imobiliaria|imoveis|restaurante|pizzaria|hamburgueria|barbearia|"
                     r"salao de beleza|frete|ltda|cnpj|loja fisica|nossa loja|empresa local|produto servico|"
                     r"boutique|shopping|supermercados?|atacadao|magazine|calcados|moveis|concessionaria|"
                     r"autoescola|agropecuaria|madeireira|farmacia|drogaria|otica|joalheria|pousada|hotel|"
                     r"balneario|resort|parque aquatico|loja|lojas)\b")


# Só para a fila (antes de ler o perfil): no @ ou no nome, estas palavras indicam negócio, não pessoa nem página.
RE_NOME_NEGOCIO = re.compile(r"(chacara|eventos|buffet|espaco|loja|store|boutique|imoveis|imobiliaria|clinica|"
                             r"odonto|studio|estudio|academia|autopecas|materiais|distribuidora|atacad|supermercado|"
                             r"farmacia|drogaria|petshop|restaurante|pizzaria|hamburgueria|lanchonete|pousada|hotel|"
                             r"barbearia|estetica|cosmeticos|semijoias|otica|veiculos|seminovos|consorcio|"
                             r"construtora|engenharia|advocacia|contabil|aluguel|delivery)")


# Foco "influenciadores": pessoas que criam conteúdo. A categoria que a própria conta escolhe no Instagram é o melhor
# sinal ("Digital creator", "Blogger", "Artist"...); a bio é o segundo.
RE_CATEGORIA_CRIADOR = re.compile(r"creator|criador|blog|artist|artista|public figure|figura publica|personalidade|"
                                  r"musician|musico|cantor|actor|ator|atriz|athlete|atleta|comedian|humor|gamer|reel|"
                                  r"influenc|fotograf|photograph|model|modelo|personal trainer|dancer|danc|youtuber|"
                                  r"apresentador|host")
RE_BIO_CRIADOR = re.compile(r"influenc|criador(a)? de conteudo|criadora? digital|blogueir|humorista|comediante|youtuber|"
                            r"tiktoker|digital creator|\bcantor(a)?\b|\batleta\b|apresentador(a)?|\bmodelo\b|"
                            r"\bator\b|\batriz\b|\bdj\b|maquiador(a)?")
# "Publi" e "parcerias" aparecem em bio de portal também: só contam se o nome for de pessoa.
RE_BIO_PUBLI = re.compile(r"\bpubli\b|parcerias?|contato comercial")
# Categoria de veículo, empresa, governo ou comunidade: não é pessoa.
RE_CATEGORIA_NAO_PESSOA = re.compile(r"noticia|news|media|midia|jornal|newspaper|radio|\btv\b|televis|empresa|company|"
                                     r"loja|store|shop|restaur|comunidade|community|governo|government|organiza|"
                                     r"servico|service|brand|marca|produto|product|agencia|agency")
# No @ ou no nome: cara de página (notícia, divulgação, vagas, "mil grau"), não de pessoa.
RE_NOME_PAGINA = re.compile(r"noticia|news|jornal|portal|plantao|urgente|acontece|online|city|tv|radio|divulga|"
                            r"empregos|vagas|classificados|milgrau|turismo|agenda|informa|alerta|emfoco|hoje|"
                            r"24h|24horas|oficialdo|dacidade|diario|comenta|fofoc|praia|acqua|aquatic|parque|park")
# Tipos da IA que não são pessoa criadora de conteúdo (políticos ficam de fora: o pedido é por influenciadores).
TIPOS_NAO_INFLUENCIADOR = ["portal_noticias", "orgao_publico", "pagina_cidade_comunidade", "perfil_fofoca",
                           "policial_plantao", "politico"]
FOCOS = ("paginas", "influenciadores")
# Nome de pessoa: duas a quatro palavras só de letras ("Ana Souza", "Luiz Felipe Castro"), já sem acento.
RE_NOME_PESSOA = re.compile(r"^[a-z]+( [a-z]+){1,3}$")


def parece_influenciador(conta: dict) -> bool:
    """Pessoa que cria conteúdo, pela IA ou, sem ela, pela categoria do perfil ou pela bio; nunca loja ou instituição."""
    tipo = conta.get("tipo")
    if tipo == "influenciador_local":
        return True
    if tipo in TIPOS_NAO_INFLUENCIADOR:
        return False
    if tipo == "irrelevante" and (conta.get("classificada_em") or "") >= PROMPT_PESSOAS_DESDE:
        return False  # o prompt novo já reconhece criadores; se ainda assim disse irrelevante, não é
    nome_compacto = mun.compactar(f"{conta['username']} {conta.get('nome') or ''}")
    if RE_NOME_NEGOCIO.search(nome_compacto) or RE_NOME_PAGINA.search(nome_compacto) or instituicao_ou_loja(conta):
        return False
    categoria = mun.normalizar(conta.get("categoria") or "")
    if RE_CATEGORIA_NAO_PESSOA.search(categoria):
        return False
    if RE_CATEGORIA_CRIADOR.search(categoria):
        return True
    bio = mun.normalizar(f"{conta.get('nome') or ''} {conta.get('bio') or ''}")
    nome_de_pessoa = bool(RE_NOME_PESSOA.match(mun.normalizar(conta.get("nome") or "")))
    return bool(RE_BIO_CRIADOR.search(bio) or (nome_de_pessoa and RE_BIO_PUBLI.search(bio)))


def cita_to(texto: str, municipios) -> bool:
    return bool(mun.no_texto(texto, municipios)) or bool(RE_TOCANTINS.search(mun.normalizar(texto)))


def instituicao_ou_loja(conta: dict) -> str | None:
    texto = mun.normalizar(f"{conta.get('nome') or ''} {conta.get('bio') or ''} {conta.get('categoria') or ''}")
    if RE_INSTITUICAO.search(texto):
        return "instituicao"
    if RE_LOJA.search(texto):
        return "comercial"
    return None


def contas_do_to(con) -> set[str]:
    """Contas da base com município do TO, pela IA ou pelo nome, bio ou @."""
    return {l[0] for l in con.execute(
        "SELECT DISTINCT username FROM conta_municipio WHERE rede = 'instagram' AND fonte IN ('ia', 'bio')")}


def ligacoes(con, do_to: set[str]) -> Counter:
    """Quantas contas do TO apontam cada perfil como parecido."""
    contagem = Counter()
    for origem, relacionada in con.execute("SELECT origem, relacionada FROM relacionadas WHERE rede = 'instagram'"):
        if origem in do_to:
            contagem[relacionada] += 1
    return contagem


def evidencia_to(con, conta: dict, municipios, apontadas: Counter) -> bool:
    """O nome ou a bio cita o estado ou um município, os posts recentes são marcados em cidades do TO, ou contas
    do TO apontam o perfil como parecido. Basta somar 2 pontos."""
    pontos = 2 if cita_to(f"{conta.get('nome') or ''} {conta.get('bio') or ''}", municipios) else 0
    pontos += 1 if mun.no_username(conta["username"], municipios) else 0
    locais = [l[0] for l in con.execute(
        "SELECT local FROM posts WHERE rede = 'instagram' AND username = ? AND local IS NOT NULL "
        "ORDER BY publicado_em DESC LIMIT 12", (conta["username"],))]
    pontos += 2 if sum(1 for local in locais if cita_to(local, municipios)) >= 2 else 0
    pontos += min(apontadas.get(conta["username"], 0), 2)
    return pontos >= 2


def aceitas(con, municipios, apontadas: Counter, piso: int, foco: str = "paginas") -> list[dict]:
    """Perfis lidos que podem entrar na lista, do mais ao menos seguido: públicos, não descartados, com evidência de
    TO e, se já classificados, de um tipo que entra na lista. Com foco em influenciadores, só pessoas criadoras."""
    if foco == "influenciadores":
        return _aceitas_influenciadores(con, municipios, apontadas, piso)
    linhas = con.execute(
        f"""SELECT c.* FROM contas c
            LEFT JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username
            LEFT JOIN revisao r ON r.rede = c.rede AND r.username = c.username
            WHERE c.rede = 'instagram' AND c.completo = 1 AND c.descartada IS NULL AND COALESCE(c.privada, 0) = 0
              AND COALESCE(r.status, 'pendente') != 'rejeitada' AND c.seguidores >= ?
              AND (k.tipo IS NULL OR (k.tipo IN ({', '.join('?' * len(TIPOS_LISTA))}) AND {SQL_DO_TO_PELA_IA}))
            ORDER BY c.seguidores DESC""", (piso, *TIPOS_LISTA)).fetchall()
    return [dict(l) for l in linhas if not instituicao_ou_loja(dict(l))
            and evidencia_to(con, dict(l), municipios, apontadas)]


# Pela IA, a conta é do TO se ela a ligou a um município do TO ou lhe deu abrangência estadual (governador, senadora,
# portal do estado todo), a mesma regra de classificacao.SQL_RELEVANTE.
SQL_DO_TO_PELA_IA = monitoramento.SQL_DO_TO_PELA_IA


def _aceitas_influenciadores(con, municipios, apontadas: Counter, piso: int) -> list[dict]:
    linhas = con.execute(
        f"""SELECT c.*, k.tipo, k.classificada_em, {SQL_DO_TO_PELA_IA} AS do_to_pela_ia FROM contas c
            LEFT JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username
            LEFT JOIN revisao r ON r.rede = c.rede AND r.username = c.username
            WHERE c.rede = 'instagram' AND c.completo = 1 AND COALESCE(c.privada, 0) = 0
              AND COALESCE(r.status, 'pendente') != 'rejeitada' AND c.seguidores >= ?
              AND (c.descartada IS NULL OR c.descartada IN ('comercial', 'abaixo_do_corte'))
            ORDER BY c.seguidores DESC""", (piso,)).fetchall()
    escolhidas = []
    for linha in linhas:
        conta = dict(linha)
        if not parece_influenciador(conta):
            continue
        # A IA nova já disse de onde é; sem ela, vale a evidência por regra.
        novo = conta["tipo"] and (conta["classificada_em"] or "") >= PROMPT_PESSOAS_DESDE
        if (conta["do_to_pela_ia"] if novo else evidencia_to(con, conta, municipios, apontadas)):
            escolhidas.append(conta)
    return escolhidas


def corte(lista_aceitas: list[dict], piso: int, tamanho: int = TAMANHO_LISTA) -> int:
    """Seguidores da última conta que hoje entraria na lista; o piso enquanto ainda não há contas suficientes."""
    if len(lista_aceitas) < tamanho:
        return piso
    return max(piso, lista_aceitas[tamanho - 1]["seguidores"])


def candidatos(con, municipios, apontadas: Counter, foco: str = "paginas") -> list[dict]:
    """Perfis ainda não lidos, do mais ao menos promissor. Os sem nenhum sinal ficam de fora. Com foco em
    influenciadores, `apontadas` conta só as ligações vindas de influenciadores, e nome de pessoa soma."""
    nomes = {}
    for username, nome in con.execute(
            """SELECT r.relacionada, r.nome FROM relacionadas r
               LEFT JOIN contas c ON c.rede = r.rede AND c.username = r.relacionada
               WHERE r.rede = 'instagram' AND c.username IS NULL"""):
        nomes[username] = nomes.get(username) or nome
    for username, nome in con.execute(
            "SELECT username, nome FROM contas WHERE rede = 'instagram' AND completo = 0 AND descartada IS NULL"):
        nomes[username] = nomes.get(username) or nome
    da_busca = {l[0] for l in con.execute(
        "SELECT username FROM conta_municipio WHERE rede = 'instagram' AND fonte = 'busca'")}
    verificadas = {l[0] for l in con.execute(
        "SELECT username FROM contas WHERE rede = 'instagram' AND completo = 0 AND verificada = 1")}

    fila = []
    for username, nome in nomes.items():
        prioridade = min(apontadas.get(username, 0), 5)
        prioridade += 3 if nome and cita_to(nome, municipios) else 0
        prioridade += 2 if mun.no_username(username, municipios) else 0
        prioridade += 2 if username in da_busca else 0
        prioridade += 3 if username in verificadas else 0
        if foco == "influenciadores":
            prioridade += min(apontadas.get(username, 0), 5)  # peso dobrado: quem influenciadores apontam
            prioridade += 2 if nome and RE_NOME_PESSOA.match(mun.normalizar(nome)) else 0
        negocio = RE_NOME_NEGOCIO.search(mun.compactar(f"{username} {nome or ''}"))
        prioridade -= 5 if negocio or instituicao_ou_loja({"nome": nome}) else 0
        if prioridade >= PRIORIDADE_MINIMA:
            fila.append({"username": username, "prioridade": prioridade, "citada_por": apontadas.get(username, 0)})
    return sorted(fila, key=lambda c: (-c["prioridade"], -c["citada_por"], c["username"]))


def termos_de_pesquisa(municipios) -> list[str]:
    por_populacao = sorted(municipios, key=lambda m: -(m["populacao"] or 0))
    return TERMOS + [m["nome"] for m in por_populacao]


def _registrar_da_busca(con, usuario: dict, ibge_id: int | None) -> None:
    db.upsert_conta(con, {"username": usuario["username"], "nome": usuario["nome"], "ig_id": usuario["id"],
                          "verificada": int(usuario["verificada"]), "origem": ORIGEM, "profundidade": 1})
    if ibge_id:
        db.vincular_municipio(con, usuario["username"], ibge_id, "busca")


def _ingerir_lidos(con, itens: list[dict], municipios, profundidade: int) -> set[str]:
    """Grava os perfis lidos (e as contas parecidas que vêm com eles) e marca que as sugestões já foram vistas."""
    lidos = set()
    for item in itens:
        username = perfis.ingerir(con, item, ORIGEM, municipios, profundidade)
        if username:
            lidos.add(username)
            con.execute("INSERT OR REPLACE INTO semelhantes (rede, username, n, lido_em) VALUES ('instagram', ?, ?, ?)",
                        (username, len(item.get("relatedProfiles") or []), db.agora()))
    return lidos


def rodada(con, navegador, orcamento: int, piso: int = CORTE_INICIAL, aviso=print,
           tamanho: int = TAMANHO_LISTA, foco: str = "paginas") -> dict:
    """Uma rodada da descoberta, com até `orcamento` leituras pelo navegador. `tamanho` é a meta (o corte é a conta
    nessa posição)."""
    municipios = mun.carregar(con)
    inicio = navegador.leituras
    usadas = lambda: navegador.leituras - inicio  # noqa: E731
    resumo = Counter()
    do_to = contas_do_to(con)
    apontadas = ligacoes(con, do_to)
    corte_atual = corte(aceitas(con, municipios, apontadas, piso, foco), piso, tamanho)

    # 1. Busca do Instagram.
    feitas = db.termos_executados(con, "pesquisa_nav")
    for termo in termos_de_pesquisa(municipios):
        if termo in feitas:
            continue
        if usadas() >= orcamento * FATIA_BUSCA:
            break
        usuarios = navegador.pesquisar(termo)
        ibge_id = next(iter(mun.no_texto(termo, municipios)), None)
        for usuario in usuarios:
            if not usuario["privada"]:
                _registrar_da_busca(con, usuario, ibge_id)
        db.registrar_busca(con, "pesquisa_nav", termo, ibge_id, None, len(usuarios))
        con.commit()
        resumo["pesquisas"] += 1

    # 2. Página das maiores contas do TO que ainda não têm sugestões guardadas (nem pela Apify nem por visita).
    com_sugestoes = {l[0] for l in con.execute("SELECT DISTINCT origem FROM relacionadas WHERE rede = 'instagram'")}
    com_sugestoes |= {l[0] for l in con.execute("SELECT username FROM semelhantes WHERE rede = 'instagram'")}
    sementes = [c for c in aceitas(con, municipios, apontadas, corte_atual, foco)
                if c["username"] not in com_sugestoes]
    for semente in sementes:
        if usadas() >= orcamento * FATIA_SEMELHANTES:
            break
        _, itens = navegador.rodar(config.ATOR_PERFIL, {"usernames": [semente["username"]]}, "perfil")
        _ingerir_lidos(con, itens, municipios, semente["profundidade"])
        resumo["semelhantes"] += 1
    if resumo["semelhantes"]:
        apontadas = ligacoes(con, do_to)

    # 3. Perfis dos candidatos mais promissores.
    if foco == "influenciadores":  # na fila, valem as ligações vindas dos influenciadores já aceitos
        influenciadores = {c["username"] for c in aceitas(con, municipios, apontadas, piso, foco)}
        fila = candidatos(con, municipios, ligacoes(con, influenciadores), foco)
    else:
        fila = candidatos(con, municipios, apontadas)
    while fila and usadas() < orcamento:
        lote, fila = fila[:navegador.lote_maximo], fila[navegador.lote_maximo:]
        _, itens = navegador.rodar(config.ATOR_PERFIL, {"usernames": [c["username"] for c in lote]}, "perfil")
        lidos = _ingerir_lidos(con, itens, municipios, 1)
        for c in lote:
            if c["username"] not in lidos:
                db.upsert_conta(con, {"username": c["username"], "origem": ORIGEM, "profundidade": 1})
                con.execute("UPDATE contas SET descartada = 'nao_encontrada' WHERE rede = 'instagram' "
                            "AND username = ?", (c["username"],))
        con.commit()
        resumo["perfis"] += len(lidos)
        if resumo["perfis"] % 100 < len(lidos):
            aviso(f"Influentes: {resumo['perfis']} perfis lidos nesta rodada...")

    # 4. Triagem por regra, com o corte atualizado.
    apontadas = ligacoes(con, contas_do_to(con))
    lista_aceitas = aceitas(con, municipios, apontadas, piso, foco)
    corte_atual = corte(lista_aceitas, piso, tamanho)
    resumo.update(triagem(con, municipios, apontadas, corte(aceitas(con, municipios, apontadas, piso), piso)))
    resumo.update({"leituras": usadas(), "candidatos_na_fila": len(fila), "aceitas": len(lista_aceitas),
                   "corte": corte_atual})
    return dict(resumo)


def triagem(con, municipios, apontadas: Counter, corte_atual: int) -> Counter:
    """Marca, sem IA, os perfis da descoberta que não podem entrar na lista. Volta para a IA as contas do TO acima
    do corte que o prompt antigo classificou como irrelevantes."""
    motivos = Counter()
    # Se o piso baixou, quem tinha ficado abaixo do corte volta para a disputa.
    con.execute("UPDATE contas SET descartada = NULL WHERE rede = 'instagram' AND descartada = 'abaixo_do_corte' "
                "AND seguidores >= ?", (corte_atual,))
    pendentes = con.execute(
        """SELECT * FROM contas c WHERE rede = 'instagram' AND origem = ? AND completo = 1 AND descartada IS NULL
           AND NOT EXISTS (SELECT 1 FROM classificacoes k WHERE k.rede = c.rede AND k.username = c.username)""",
        (ORIGEM,)).fetchall()
    for linha in pendentes:
        conta = dict(linha)
        motivo = ("privada" if conta["privada"]
                  else "abaixo_do_corte" if (conta["seguidores"] or 0) < corte_atual
                  else instituicao_ou_loja(conta)
                  or (None if evidencia_to(con, conta, municipios, apontadas) else "fora_do_to"))
        if motivo:
            con.execute("UPDATE contas SET descartada = ? WHERE rede = 'instagram' AND username = ?",
                        (motivo, conta["username"]))
            motivos[motivo] += 1

    antigas = con.execute(
        """SELECT c.* FROM contas c JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username
           WHERE c.rede = 'instagram' AND k.tipo = 'irrelevante' AND k.classificada_em < ? AND c.seguidores >= ?
             AND c.descartada IS NULL AND COALESCE(c.privada, 0) = 0""",
        (PROMPT_PESSOAS_DESDE, corte_atual)).fetchall()
    for linha in antigas:
        conta = dict(linha)
        if not instituicao_ou_loja(conta) and evidencia_to(con, conta, municipios, apontadas):
            con.execute("DELETE FROM classificacoes WHERE rede = 'instagram' AND username = ?", (conta["username"],))
            motivos["volta_para_a_ia"] += 1
    con.commit()
    return motivos


def situacao(con, piso: int = CORTE_INICIAL) -> dict:
    """Retrato da descoberta, sem nenhuma leitura."""
    municipios = mun.carregar(con)
    apontadas = ligacoes(con, contas_do_to(con))
    lista_aceitas = aceitas(con, municipios, apontadas, piso)
    feitas = db.termos_executados(con, "pesquisa_nav")
    return {
        "pesquisas_feitas": len(feitas), "pesquisas_total": len(termos_de_pesquisa(municipios)),
        "candidatos_na_fila": len(candidatos(con, municipios, apontadas)),
        "perfis_lidos": con.execute("SELECT COUNT(*) FROM contas WHERE rede = 'instagram' AND origem = ? "
                                    "AND completo = 1", (ORIGEM,)).fetchone()[0],
        "aceitas": len(lista_aceitas), "corte": corte(lista_aceitas, piso),
        "sem_classificacao": sum(1 for c in lista_aceitas if not con.execute(
            "SELECT 1 FROM classificacoes WHERE rede = 'instagram' AND username = ?", (c["username"],)).fetchone()),
        "na_lista": con.execute("SELECT COUNT(*) FROM listas WHERE lista = ?", (LISTA,)).fetchone()[0],
    }


def ranking_influenciadores(con, tamanho: int = 100) -> list[dict]:
    """Os influenciadores do TO com mais seguidores. Sem a classificação nova da IA, ficam "a confirmar"."""
    municipios = mun.carregar(con)
    lista = _aceitas_influenciadores(con, municipios, ligacoes(con, contas_do_to(con)), 0)[:tamanho]
    for conta in lista:
        conta["confirmada"] = conta["tipo"] == "influenciador_local" \
            and (conta["classificada_em"] or "") >= PROMPT_PESSOAS_DESDE
        conta["municipios_ia"] = ", ".join(l[0] for l in con.execute(
            "SELECT m.nome FROM conta_municipio cm JOIN municipios m ON m.ibge_id = cm.ibge_id "
            "WHERE cm.rede = 'instagram' AND cm.username = ? AND cm.fonte = 'ia'", (conta["username"],)))
    return lista


def a_confirmar(con, tamanho: int = 300) -> list[dict]:
    """Influenciadores do ranking que ainda não passaram pela IA com o prompt novo (os que o `classificar` deve ver)."""
    return [c for c in ranking_influenciadores(con, tamanho) if not c["confirmada"]]


def ranking(con, tamanho: int = 100, com_orgaos: bool = True) -> list[dict]:
    """As contas do TO com mais seguidores, sem empresas. Já classificadas: a IA as ligou a um município do TO e não
    as marcou como irrelevantes (comércio, serviço, outro estado). Ainda sem classificação: evidência de TO por regra
    e nada de loja no nome, na bio ou na categoria; ficam marcadas como "a confirmar" até o `classificar`."""
    municipios = mun.carregar(con)
    apontadas = ligacoes(con, contas_do_to(con))
    tipos = TIPOS_LISTA + (["orgao_publico"] if com_orgaos else [])
    linhas = con.execute(
        f"""SELECT c.*, k.tipo,
                   (SELECT GROUP_CONCAT(m.nome, ', ') FROM conta_municipio cm JOIN municipios m ON m.ibge_id = cm.ibge_id
                     WHERE cm.rede = c.rede AND cm.username = c.username AND cm.fonte = 'ia') AS municipios_ia
            FROM contas c
            LEFT JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username
            LEFT JOIN revisao r ON r.rede = c.rede AND r.username = c.username
            WHERE c.rede = 'instagram' AND c.completo = 1 AND c.descartada IS NULL AND COALESCE(c.privada, 0) = 0
              AND COALESCE(r.status, 'pendente') != 'rejeitada'
              AND (k.tipo IS NULL OR (k.tipo IN ({', '.join('?' * len(tipos))}) AND {SQL_DO_TO_PELA_IA}))
            ORDER BY c.seguidores DESC""", tipos).fetchall()
    escolhidas = []
    for linha in linhas:
        conta = dict(linha)
        if conta["tipo"] is None:
            negocio = RE_NOME_NEGOCIO.search(mun.compactar(f"{conta['username']} {conta['nome'] or ''}"))
            if negocio or instituicao_ou_loja(conta) == "comercial" \
                    or not evidencia_to(con, conta, municipios, apontadas):
                continue
            if not com_orgaos and instituicao_ou_loja(conta):
                continue
        escolhidas.append(conta)
        if len(escolhidas) >= tamanho:
            break
    return escolhidas


def montar_lista(con, tamanho: int = TAMANHO_LISTA) -> list[dict]:
    """As `tamanho` contas com município do TO (pela IA) e mais seguidores, dos tipos de TIPOS_LISTA. Elas entram
    na base monitorada; a lista anterior é substituída."""
    linhas = con.execute(
        f"""SELECT c.username, c.nome, c.seguidores, k.tipo FROM contas c
            JOIN classificacoes k ON k.rede = c.rede AND k.username = c.username
            LEFT JOIN revisao r ON r.rede = c.rede AND r.username = c.username
            WHERE c.rede = 'instagram' AND k.tipo IN ({', '.join('?' * len(TIPOS_LISTA))})
              AND c.descartada IS NULL AND COALESCE(c.privada, 0) = 0
              AND COALESCE(r.status, 'pendente') != 'rejeitada'
              AND {SQL_DO_TO_PELA_IA}
            ORDER BY c.seguidores DESC LIMIT ?""", (*TIPOS_LISTA, tamanho)).fetchall()
    _gravar_lista(con, LISTA, linhas)
    return [dict(l) for l in linhas]


def montar_lista_influenciadores(con, tamanho: int = TAMANHO_INFLUENCIADORES) -> list[dict]:
    """Os `tamanho` influenciadores do ranking (confirmados pela IA ou, até o `classificar`, pela regra). Entram na
    base monitorada e na vigilância de 1 em 1 hora; a lista anterior é substituída."""
    lista = ranking_influenciadores(con, tamanho)
    _gravar_lista(con, LISTA_INFLUENCIADORES, lista)
    return lista


def _gravar_lista(con, lista: str, linhas) -> None:
    con.execute("DELETE FROM listas WHERE rede = 'instagram' AND lista = ?", (lista,))
    con.executemany(
        "INSERT INTO listas (rede, username, lista, posicao, seguidores, atualizada_em) "
        "VALUES ('instagram', ?, ?, ?, ?, ?)",
        [(l["username"], lista, i, l["seguidores"], db.agora()) for i, l in enumerate(linhas, 1)])
    con.commit()
