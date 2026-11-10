import { FlashList, ListRenderItem } from '@shopify/flash-list'
import { router } from 'expo-router'
import { useCallback, useMemo, useState } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'

import { ItemConta } from '@/componentes/ItemConta'
import { ItemMencao } from '@/componentes/ItemMencao'
import { Mapa } from '@/componentes/Mapa'
import { Municipios } from '@/componentes/Municipios'
import { Abas, Chip } from '@/componentes/Opcoes'
import { Numero, Resumo } from '@/componentes/Resumo'
import { Barras } from '@/componentes/Barras'
import { Semanas } from '@/componentes/Semanas'
import {
  agrupar,
  alternar,
  Conta,
  contarPorMunicipio,
  DADOS,
  filtrarContas,
  filtrarMencoes,
  LIMITES_CONTAS,
  LIMITES_MENCOES,
  Mencao,
  MUNICIPIO_POR_ID,
  municipiosSemConta,
  nomeConta,
  nomesMunicipios,
  Periodo,
  POPULACAO_TOTAL,
  porSemana,
  ranking,
  ROTULO_SETOR,
  ROTULO_TEMA,
  ROTULO_TIPO,
  Sentimento,
} from '@/dados'
import { compacto, momento, numero } from '@/formato'
import { Cores, criarEstilos, FONTES, MARGEM, TAMANHO, useCores } from '@/tema'

type Modo = 'mencoes' | 'contas'

const MODOS: { valor: Modo; rotulo: string }[] = [
  { valor: 'mencoes', rotulo: 'Menções' },
  { valor: 'contas', rotulo: 'Contas' },
]
const PERIODOS: { valor: Periodo; rotulo: string }[] = [
  { valor: 7, rotulo: '7 dias' },
  { valor: 30, rotulo: '30 dias' },
  { valor: 60, rotulo: '60 dias' },
  { valor: null, rotulo: 'Tudo' },
]
const TIPOS = Object.keys(ROTULO_TIPO).filter((tipo) => DADOS.contas.some((c) => c.tipo === tipo))
const SEM_CONTA = municipiosSemConta()
const RODAPE = DADOS.coletadoEm ? `Coleta de ${momento(DADOS.coletadoEm)}` : null
const MAX_MUNICIPIOS = 6
const SEMANAS = 9

function plural(n: number, um: string, varios: string): string {
  return n === 1 ? um : varios
}

function resumoMencoes(lista: Mencao[], c: Cores): { principal: Numero; numeros: Numero[] } {
  const negativas = lista.filter((m) => m.sentimento === 'negativo').length
  const energisa = lista.filter((m) => m.citaEnergisa).length
  return {
    principal: { valor: numero(lista.length), rotulo: plural(lista.length, 'menção', 'menções') },
    numeros: [
      { valor: numero(negativas), rotulo: plural(negativas, 'negativa', 'negativas'), cor: c.negativo },
      { valor: numero(lista.filter((m) => m.alcance === 'local').length), rotulo: 'com impacto só local' },
      { valor: compacto(lista.reduce((t, m) => t + m.interacoes, 0)), rotulo: 'interações' },
      { valor: numero(energisa), rotulo: plural(energisa, 'cita a Energisa', 'citam a Energisa') },
    ],
  }
}

function resumoContas(lista: Conta[], municipio: number | null): { principal: Numero; numeros: Numero[] } {
  const principal = { valor: numero(lista.length), rotulo: plural(lista.length, 'conta monitorada', 'contas monitoradas') }
  // Somar seguidores contaria a mesma pessoa várias vezes; o tamanho das contas grandes diz mais sobre o alcance.
  const grandes = lista.filter((c) => (c.seguidores ?? 0) >= 100_000).length
  const nGrandes = { valor: numero(grandes), rotulo: 'com mais de 100 mil seguidores' }
  const maior = { valor: compacto(Math.max(0, ...lista.map((c) => c.seguidores ?? 0))), rotulo: 'seguidores na maior' }
  const portais = lista.filter((c) => c.tipo === 'portal_noticias').length
  const nPortais = { valor: numero(portais), rotulo: plural(portais, 'portal', 'portais') }
  if (municipio != null) return { principal, numeros: [nPortais, maior] }
  const cobertos = new Set(lista.flatMap((c) => c.municipios))
  const populacao = DADOS.municipios.filter((m) => cobertos.has(m.id)).reduce((t, m) => t + (m.populacao ?? 0), 0)
  return {
    principal,
    numeros: [
      { valor: numero(cobertos.size), rotulo: `de ${DADOS.municipios.length} municípios` },
      { valor: `${Math.round((100 * populacao) / POPULACAO_TOTAL)}%`, rotulo: 'da população' },
      nPortais,
      nGrandes,
    ],
  }
}

function chaveMencao(m: Mencao) {
  return m.id
}

function chaveConta(c: Conta) {
  return c.username
}

export default function Inicio() {
  const s = useEstilos()
  const c = useCores()
  const [modo, setModo] = useState<Modo>('mencoes')
  const [periodo, setPeriodo] = useState<Periodo>(null)
  const [sentimentos, setSentimentos] = useState<Sentimento[]>([])
  const [tema, setTema] = useState<string | null>(null)
  const [setor, setSetor] = useState<string | null>(null)
  const [municipio, setMunicipio] = useState<number | null>(null)
  const [tipos, setTipos] = useState<string[]>([])

  // Cada gráfico ignora o próprio filtro: o mapa mostra todos os municípios, as barras todos os temas e setores.
  const filtro = useMemo(
    () => ({ periodo, sentimentos, tema, setor, municipio }),
    [periodo, sentimentos, tema, setor, municipio],
  )
  const mencoes = useMemo(() => filtrarMencoes(filtro), [filtro])
  const mencoesNoMapa = useMemo(() => contarPorMunicipio(filtrarMencoes(filtro, 'municipio')), [filtro])
  const temas = useMemo(() => agrupar(filtrarMencoes(filtro, 'tema'), 'tema'), [filtro])
  const setores = useMemo(() => agrupar(filtrarMencoes(filtro, 'setor'), 'setor'), [filtro])
  const semanas = useMemo(() => porSemana(filtrarMencoes(filtro, 'periodo'), SEMANAS), [filtro])
  const contas = useMemo(() => filtrarContas(tipos, municipio), [tipos, municipio])
  const contasNoMapa = useMemo(() => contarPorMunicipio(filtrarContas(tipos, null)), [tipos])

  const valoresMapa = modo === 'mencoes' ? mencoesNoMapa : contasNoMapa
  const limites = modo === 'mencoes' ? LIMITES_MENCOES : LIMITES_CONTAS
  const destaques = useMemo(() => {
    const lista = ranking(valoresMapa, MAX_MUNICIPIOS)
    if (municipio != null && !lista.some((m) => m.id === municipio)) {
      lista.push({ id: municipio, total: valoresMapa.get(municipio) ?? 0 })
    }
    return lista
  }, [valoresMapa, municipio])

  const selecionarMunicipio = useCallback((id: number) => setMunicipio((atual) => (atual === id ? null : id)), [])
  const verEstado = useCallback(() => setMunicipio(null), [])
  const selecionarTema = useCallback((t: string) => setTema((atual) => (atual === t ? null : t)), [])
  const selecionarSetor = useCallback((st: string) => setSetor((atual) => (atual === st ? null : st)), [])
  const alternarSentimento = useCallback((st: Sentimento) => setSentimentos((lista) => alternar(lista, st)), [])
  const abrirMencao = useCallback((id: string) => router.push({ pathname: '/mencao/[id]', params: { id } }), [])
  const limparFiltros = useCallback(() => {
    setPeriodo(null)
    setSentimentos([])
    setTema(null)
    setSetor(null)
    setMunicipio(null)
    setTipos([])
  }, [])

  const renderMencao: ListRenderItem<Mencao> = useCallback(
    ({ item }) => (
      <ItemMencao
        id={item.id}
        data={item.data}
        conta={nomeConta(item)}
        story={item.formato === 'Story'}
        resumo={item.resumo}
        sentimento={item.sentimento}
        tema={item.tema}
        setor={item.setor}
        alcance={item.alcance}
        interacoes={item.interacoes}
        indice={item.indice}
        reacao={item.reacao}
        onAbrir={abrirMencao}
      />
    ),
    [abrirMencao],
  )

  const renderConta: ListRenderItem<Conta> = useCallback(
    ({ item }) => (
      <ItemConta
        username={item.username}
        nome={item.nome}
        tipo={item.tipo}
        seguidores={item.seguidores}
        municipios={nomesMunicipios(item.municipios)}
      />
    ),
    [],
  )

  const resumo = modo === 'mencoes' ? resumoMencoes(mencoes, c) : resumoContas(contas, municipio)

  const cabecalho = (
    <View style={s.cabeca}>
      <View style={s.faixaAbas}>
        <Abas opcoes={MODOS} valor={modo} onMudar={setModo} />
      </View>

      {modo === 'mencoes' ? (
        <View style={s.margem}>
          <Abas opcoes={PERIODOS} valor={periodo} onMudar={setPeriodo} pequenas />
        </View>
      ) : (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={s.rolagem} contentContainerStyle={s.chips}>
          {TIPOS.map((tipo) => (
            <Chip
              key={tipo}
              rotulo={ROTULO_TIPO[tipo]}
              ativo={tipos.includes(tipo)}
              onPress={() => setTipos((lista) => alternar(lista, tipo))}
            />
          ))}
        </ScrollView>
      )}

      <View style={s.painel}>
        <View style={s.colunaMapa}>
          <Mapa valores={valoresMapa} limites={limites} selecionado={municipio} onSelecionar={selecionarMunicipio} />
        </View>
        <Resumo
          principal={resumo.principal}
          numeros={resumo.numeros}
          municipio={municipio == null ? undefined : MUNICIPIO_POR_ID.get(municipio)?.nome}
          onLimpar={verEstado}
          rodape={RODAPE}
        />
      </View>

      <View style={s.margem}>
        <Municipios linhas={destaques} limites={limites} selecionado={municipio} onSelecionar={selecionarMunicipio} />
      </View>

      <View style={s.margem}>
        {modo === 'mencoes' ? (
          <View style={s.blocos}>
            <Semanas linhas={semanas} />
            <Barras
              titulo="Temas"
              rotulos={ROTULO_TEMA}
              linhas={temas}
              selecionado={tema}
              onSelecionar={selecionarTema}
              sentimentos={sentimentos}
              onAlternarSentimento={alternarSentimento}
            />
            <Barras
              titulo="Setores da Energisa"
              rotulos={ROTULO_SETOR}
              linhas={setores}
              selecionado={setor}
              onSelecionar={selecionarSetor}
            />
          </View>
        ) : (
          <View style={s.bloco}>
            <Text style={s.tituloBloco}>{SEM_CONTA.length} municípios sem conta monitorada</Text>
            <Text style={s.texto}>{SEM_CONTA.map((m) => m.nome).join(', ')}</Text>
          </View>
        )}
      </View>

      <Text style={s.titulo}>{modo === 'mencoes' ? 'Posts' : 'Perfis'}</Text>
    </View>
  )

  const vazio = (
    <View style={s.vazio}>
      <Text style={s.texto}>
        {modo === 'mencoes' ? 'Nenhum post com esses filtros.' : 'Nenhuma conta monitorada com esses filtros.'}
      </Text>
      <Pressable onPress={limparFiltros} hitSlop={8} accessibilityRole="button">
        <Text style={s.acao}>Limpar filtros</Text>
      </Pressable>
    </View>
  )

  if (modo === 'mencoes') {
    return (
      <FlashList
        data={mencoes}
        keyExtractor={chaveMencao}
        renderItem={renderMencao}
        ListHeaderComponent={cabecalho}
        ListEmptyComponent={vazio}
        contentInsetAdjustmentBehavior="automatic"
        style={s.tela}
      />
    )
  }
  return (
    <FlashList
      data={contas}
      keyExtractor={chaveConta}
      renderItem={renderConta}
      ListHeaderComponent={cabecalho}
      ListEmptyComponent={vazio}
      contentInsetAdjustmentBehavior="automatic"
      style={s.tela}
    />
  )
}

const useEstilos = criarEstilos((c) => {
  const texto = { fontFamily: FONTES.texto, fontSize: TAMANHO.texto, lineHeight: 22, color: c.tinta2 } as const
  const titulo = { fontFamily: FONTES.forte, fontSize: TAMANHO.texto, color: c.tinta } as const
  return {
    tela: { backgroundColor: c.tela },
    cabeca: { width: '100%', paddingTop: 4, gap: 28 },
    faixaAbas: {
      paddingHorizontal: MARGEM,
      borderBottomWidth: 1,
      borderBottomColor: c.linha,
    },
    margem: { paddingHorizontal: MARGEM },
    // As faixas de chips rolam até a borda da tela.
    rolagem: { flexGrow: 0, width: '100%' },
    chips: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: MARGEM },
    painel: { flexDirection: 'row', alignItems: 'flex-start', gap: 20, paddingHorizontal: MARGEM },
    colunaMapa: { width: '52%' },
    bloco: { gap: 8 },
    blocos: { gap: 28 },
    titulo: { ...titulo, paddingHorizontal: MARGEM },
    tituloBloco: titulo,
    texto,
    vazio: { gap: 8, paddingHorizontal: MARGEM, paddingVertical: 24 },
    acao: { ...texto, fontFamily: FONTES.medio, color: c.destaque },
  }
})
