import { Stack, useLocalSearchParams } from 'expo-router'
import { Linking, Pressable, ScrollView, Text, View } from 'react-native'

import {
  Mencao,
  MENCAO_POR_ID,
  nomeConta,
  nomesMunicipios,
  ROTULO_ALCANCE,
  ROTULO_REACAO,
  ROTULO_SENTIMENTO,
  ROTULO_SETOR,
  ROTULO_TEMA,
  ROTULO_TIPO,
} from '@/dados'
import { compacto, dataLonga, numero, porcento, vezes } from '@/formato'
import { criarEstilos, FONTES, MARGEM, TAMANHO } from '@/tema'

type Linha = { rotulo: string; valor: string }

function engajamento(m: Mencao): Linha[] {
  const linhas: Linha[] = [
    { rotulo: 'Interações', valor: numero(m.interacoes) },
    { rotulo: 'Curtidas', valor: m.curtidas == null ? 'ocultas' : numero(m.curtidas) },
    { rotulo: 'Comentários', valor: numero(m.comentarios ?? 0) },
  ]
  if (m.visualizacoes != null) linhas.push({ rotulo: 'Visualizações', valor: compacto(m.visualizacoes) })
  // Passa de 100% quando o post viraliza: as interações vêm também de quem não segue a conta.
  if (m.engajamentoPct != null) linhas.push({ rotulo: 'Interações ÷ seguidores', valor: porcento(m.engajamentoPct) })
  if (m.indice != null) linhas.push({ rotulo: 'Em relação à média da conta', valor: vezes(m.indice) })
  return linhas
}

function repercussao(m: Mencao): Linha[] {
  const linhas: Linha[] = [
    { rotulo: 'Contas que trataram do assunto', valor: numero(m.repercutiram) },
    { rotulo: 'Cobertura estadual', valor: m.coberturaEstadual ? 'Sim' : 'Não' },
  ]
  if (m.seguidores != null) linhas.push({ rotulo: 'Seguidores da conta', valor: compacto(m.seguidores) })
  return linhas
}

function Registro({ titulo, linhas }: { titulo: string; linhas: Linha[] }) {
  const s = useEstilos()
  return (
    <View>
      <Text style={s.titulo}>{titulo}</Text>
      {linhas.map((l) => (
        <View key={l.rotulo} style={s.registro}>
          <Text style={s.rotulo}>{l.rotulo}</Text>
          <Text style={s.valor}>{l.valor}</Text>
        </View>
      ))}
    </View>
  )
}

export default function Detalhe() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const s = useEstilos()
  const m = MENCAO_POR_ID.get(id)

  if (!m) {
    return (
      <View style={s.conteudo}>
        <Stack.Screen options={{ title: 'Post' }} />
        <Text style={s.texto}>
          Este post não está nos dados atuais. Gere o arquivo de novo com python -m radar exportar-app.
        </Text>
      </View>
    )
  }

  const link = m.link
  const municipios = nomesMunicipios(m.municipios)
  return (
    <ScrollView style={s.tela} contentInsetAdjustmentBehavior="automatic" contentContainerStyle={s.conteudo}>
      <Stack.Screen options={{ title: nomeConta(m) }} />

      <View style={s.cabeca}>
        <View style={s.etiquetas}>
          <View style={s.sentimento}>
            <View style={s[m.sentimento]} />
            <Text style={s.forte}>{ROTULO_SENTIMENTO[m.sentimento]}</Text>
          </View>
          <Text style={s.meta}>{ROTULO_TEMA[m.tema]}</Text>
          {m.setor ? <Text style={s.meta}>Setor: {ROTULO_SETOR[m.setor] ?? m.setor}</Text> : null}
          <Text style={s.meta}>Impacto {ROTULO_ALCANCE[m.alcance].toLowerCase()}</Text>
          {m.formato === 'Story' ? <Text style={s.forte}>Story</Text> : null}
        </View>
        <Text style={s.resumo}>{m.resumo}</Text>
        <View style={s.etiquetas}>
          <Text style={s.meta}>{dataLonga(m.data)}</Text>
          {m.tipoConta ? <Text style={s.meta}>{ROTULO_TIPO[m.tipoConta] ?? m.tipoConta}</Text> : null}
          {m.citaEnergisa ? <Text style={s.meta}>Cita a Energisa</Text> : null}
        </View>
      </View>

      {m.reacao ? (
        <View style={s.secao}>
          <Text style={s.titulo}>{ROTULO_REACAO[m.reacao]}</Text>
          {m.reacaoResumo ? <Text style={s.texto}>{m.reacaoResumo}</Text> : null}
          {m.comentariosLidos ? (
            <Text style={s.meta}>
              Leitura de {m.comentariosLidos} {m.comentariosLidos === 1 ? 'comentário' : 'comentários'}
            </Text>
          ) : null}
        </View>
      ) : null}

      <Registro titulo="Engajamento" linhas={engajamento(m)} />
      <Registro titulo="Repercussão" linhas={repercussao(m)} />

      <View style={s.secao}>
        <Text style={s.titulo}>Municípios</Text>
        <Text style={s.texto}>{municipios || 'Não identificado'}</Text>
      </View>

      {m.legenda ? (
        <View style={s.secao}>
          <Text style={s.titulo}>Legenda</Text>
          <Text style={s.texto} selectable>
            {m.legenda}
          </Text>
        </View>
      ) : null}

      {link ? (
        <Pressable
          style={({ pressed }) => (pressed ? s.botaoPressionado : s.botao)}
          onPress={() => Linking.openURL(link)}
          accessibilityRole="link"
        >
          <Text style={s.botaoTexto}>Abrir no Instagram</Text>
        </Pressable>
      ) : null}
    </ScrollView>
  )
}

const useEstilos = criarEstilos((c) => {
  const meta = { fontFamily: FONTES.texto, fontSize: TAMANHO.meta, color: c.tinta2 } as const
  const ponto = { width: 8, height: 8, borderRadius: 4 } as const
  const botao = {
    alignItems: 'center',
    paddingVertical: 15,
    borderRadius: 14,
    borderCurve: 'continuous',
    backgroundColor: c.tinta,
  } as const
  return {
    tela: { backgroundColor: c.tela },
    conteudo: { paddingHorizontal: MARGEM, paddingTop: 8, paddingBottom: 48, gap: 32 },
    cabeca: { gap: 12 },
    etiquetas: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', columnGap: 12, rowGap: 4 },
    sentimento: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    negativo: { ...ponto, backgroundColor: c.negativo },
    neutro: { ...ponto, backgroundColor: c.neutro },
    positivo: { ...ponto, backgroundColor: c.positivo },
    meta,
    forte: { ...meta, fontFamily: FONTES.forte, color: c.tinta },
    resumo: { fontFamily: FONTES.medio, fontSize: TAMANHO.numero, lineHeight: 30, color: c.tinta },
    secao: { gap: 8 },
    titulo: { fontFamily: FONTES.forte, fontSize: TAMANHO.texto, color: c.tinta, marginBottom: 4 },
    registro: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'baseline',
      gap: 16,
      paddingVertical: 11,
      borderBottomWidth: 1,
      borderBottomColor: c.linha,
    },
    rotulo: { flex: 1, fontFamily: FONTES.texto, fontSize: TAMANHO.texto, color: c.tinta2 },
    valor: { fontFamily: FONTES.numero, fontSize: TAMANHO.aba, color: c.tinta, fontVariant: ['tabular-nums'] },
    texto: { fontFamily: FONTES.texto, fontSize: TAMANHO.texto, lineHeight: 23, color: c.tinta },
    botao,
    botaoPressionado: { ...botao, opacity: 0.85 },
    botaoTexto: { fontFamily: FONTES.forte, fontSize: TAMANHO.texto, color: c.tela },
  }
})
