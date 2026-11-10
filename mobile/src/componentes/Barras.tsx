import { memo } from 'react'
import { Pressable, Text, View } from 'react-native'

import { LinhaGrupo, ROTULO_SENTIMENTO, Sentimento, SENTIMENTOS } from '@/dados'
import { criarEstilos, FONTES, TAMANHO, useCores } from '@/tema'

type Props = {
  titulo: string
  rotulos: Record<string, string>
  linhas: LinhaGrupo[]
  selecionado: string | null
  onSelecionar: (chave: string) => void
  /** Com estes dois, a legenda de sentimentos aparece e filtra. */
  sentimentos?: Sentimento[]
  onAlternarSentimento?: (sentimento: Sentimento) => void
}

/** Uma barra por grupo (tema, setor), dividida por sentimento. Tocar num grupo filtra os posts. */
export const Barras = memo(function Barras(props: Props) {
  const { titulo, rotulos, linhas, selecionado, onSelecionar, sentimentos, onAlternarSentimento } = props
  const c = useCores()
  const s = useEstilos()
  const maximo = Math.max(1, ...linhas.map((l) => l.total))
  return (
    <View style={s.bloco}>
      <View style={s.topo}>
        <Text style={s.titulo}>{titulo}</Text>
        {sentimentos && onAlternarSentimento ? (
          <View style={s.legenda}>
            {SENTIMENTOS.map((sentimento) => {
              const ligado = sentimentos.length === 0 || sentimentos.includes(sentimento)
              return (
                <Pressable
                  key={sentimento}
                  style={s.itemLegenda}
                  onPress={() => onAlternarSentimento(sentimento)}
                  accessibilityRole="button"
                  accessibilityState={{ selected: sentimentos.includes(sentimento) }}
                  hitSlop={8}
                >
                  <View
                    style={ligado ? [s.amostra, { backgroundColor: c[sentimento] }] : [s.amostraVazia, { borderColor: c[sentimento] }]}
                  />
                  <Text style={ligado ? s.legendaAtiva : s.legendaApagada}>{ROTULO_SENTIMENTO[sentimento]}</Text>
                </Pressable>
              )
            })}
          </View>
        ) : null}
      </View>
      {linhas.map((linha) => {
        const ativo = selecionado === linha.chave
        const rotulo = rotulos[linha.chave] ?? linha.chave
        return (
          <Pressable
            key={linha.chave}
            style={selecionado != null && !ativo ? s.linhaApagada : s.linha}
            onPress={() => onSelecionar(linha.chave)}
            accessibilityRole="button"
            accessibilityState={{ selected: ativo }}
            accessibilityLabel={`${rotulo}: ${linha.total}, ${linha.negativo} negativas`}
          >
            <View style={s.cabecalho}>
              <Text style={ativo ? s.rotuloAtivo : s.rotulo}>{rotulo}</Text>
              <Text style={s.total}>{linha.total}</Text>
            </View>
            <View style={s.barra}>
              {SENTIMENTOS.filter((sentimento) => linha[sentimento] > 0).map((sentimento) => (
                <View
                  key={sentimento}
                  style={[s.segmento, { width: `${(100 * linha[sentimento]) / maximo}%`, backgroundColor: c[sentimento] }]}
                />
              ))}
            </View>
          </Pressable>
        )
      })}
    </View>
  )
})

const useEstilos = criarEstilos((c) => {
  const rotulo = { fontFamily: FONTES.texto, fontSize: TAMANHO.texto, color: c.tinta } as const
  const legenda = { fontFamily: FONTES.medio, fontSize: TAMANHO.meta, color: c.tinta2 } as const
  const linha = { gap: 8 } as const
  const amostra = { width: 10, height: 10, borderRadius: 5 } as const
  return {
    bloco: { gap: 16 },
    topo: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 },
    titulo: { fontFamily: FONTES.forte, fontSize: TAMANHO.texto, color: c.tinta },
    legenda: { flexDirection: 'row', gap: 14 },
    itemLegenda: { flexDirection: 'row', alignItems: 'center', gap: 5 },
    amostra,
    amostraVazia: { ...amostra, borderWidth: 1.5 },
    legendaAtiva: legenda,
    legendaApagada: { ...legenda, color: c.tinta3 },
    linha,
    linhaApagada: { ...linha, opacity: 0.4 },
    cabecalho: { flexDirection: 'row', justifyContent: 'space-between', gap: 12 },
    rotulo,
    rotuloAtivo: { ...rotulo, fontFamily: FONTES.forte },
    total: { ...rotulo, fontFamily: FONTES.numero, fontVariant: ['tabular-nums'] },
    // 2 px de vão entre os segmentos e pontas arredondadas, como pede a skill de visualização.
    barra: { flexDirection: 'row', gap: 2, height: 6 },
    segmento: { height: 6, borderRadius: 3, flexShrink: 1 },
  }
})
