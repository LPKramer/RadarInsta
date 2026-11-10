import { memo } from 'react'
import { Pressable, Text, View } from 'react-native'

import { ROTULO_ALCANCE, ROTULO_REACAO, ROTULO_SENTIMENTO, ROTULO_SETOR, ROTULO_TEMA, Sentimento } from '@/dados'
import { compacto, dataCurta, vezes } from '@/formato'
import { criarEstilos, FONTES, MARGEM, TAMANHO } from '@/tema'

// Só destaca a comparação com a média da conta quando o post rendeu bem acima do normal.
const INDICE_DESTAQUE = 1.5

type Props = {
  id: string
  data: string
  conta: string
  story: boolean
  resumo: string
  sentimento: Sentimento
  tema: string
  setor: string | null
  alcance: string
  interacoes: number
  indice: number | null
  reacao: string | null
  onAbrir: (id: string) => void
}

export const ItemMencao = memo(function ItemMencao(props: Props) {
  const { id, data, conta, story, resumo, sentimento, tema, setor, alcance, interacoes, indice, reacao, onAbrir } = props
  const s = useEstilos()
  const destaque = indice != null && indice >= INDICE_DESTAQUE
  return (
    <Pressable
      style={({ pressed }) => (pressed ? s.itemPressionado : s.item)}
      onPress={() => onAbrir(id)}
      accessibilityRole="button"
    >
      <View style={s.corpo}>
        <View style={s.topo}>
          <Text style={s.conta} numberOfLines={1}>
            {conta}
          </Text>
          <Text style={s.data}>{dataCurta(data)}</Text>
        </View>
        <Text style={s.resumo} numberOfLines={3}>
          {resumo}
        </Text>
        <View style={s.base}>
          <View style={s.etiquetas}>
            <View style={s.sentimento}>
              <View style={s[sentimento]} />
              <Text style={s.forte}>{ROTULO_SENTIMENTO[sentimento]}</Text>
            </View>
            <Text style={s.meta}>{ROTULO_TEMA[tema]}</Text>
            {setor ? <Text style={s.meta}>Setor: {ROTULO_SETOR[setor] ?? setor}</Text> : null}
            <Text style={s.meta}>{ROTULO_ALCANCE[alcance]}</Text>
            {story ? <Text style={s.forte}>Story</Text> : null}
          </View>
          <Text style={s.numero}>
            {compacto(interacoes)}
            <Text style={s.meta}> {interacoes === 1 ? 'interação' : 'interações'}</Text>
          </Text>
        </View>
        {reacao || destaque ? (
          <View style={s.etiquetas}>
            {reacao ? <Text style={s.meta}>{ROTULO_REACAO[reacao]}</Text> : null}
            {destaque ? <Text style={s.forte}>{vezes(indice ?? 0)} a média da conta</Text> : null}
          </View>
        ) : null}
      </View>
    </Pressable>
  )
})

const useEstilos = criarEstilos((c) => {
  const item = { paddingLeft: MARGEM, backgroundColor: c.tela } as const
  const meta = { fontFamily: FONTES.texto, fontSize: TAMANHO.meta, color: c.tinta2 } as const
  // Ponto na cor do sentimento: a cor identifica, a palavra ao lado nomeia.
  const ponto = { width: 8, height: 8, borderRadius: 4 } as const
  return {
    item,
    itemPressionado: { ...item, backgroundColor: c.campo },
    // A linha divisória começa no texto e vai até a borda da tela.
    corpo: {
      gap: 8,
      paddingVertical: 16,
      paddingRight: MARGEM,
      borderBottomWidth: 1,
      borderBottomColor: c.linha,
    },
    topo: { flexDirection: 'row', alignItems: 'baseline', gap: 12 },
    conta: { flex: 1, fontFamily: FONTES.forte, fontSize: TAMANHO.texto, color: c.tinta },
    data: { ...meta, color: c.tinta3, fontVariant: ['tabular-nums'] },
    resumo: { fontFamily: FONTES.texto, fontSize: TAMANHO.texto, lineHeight: 22, color: c.tinta },
    base: { flexDirection: 'row', alignItems: 'center', gap: 12 },
    etiquetas: { flex: 1, flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', columnGap: 12, rowGap: 4 },
    sentimento: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    negativo: { ...ponto, backgroundColor: c.negativo },
    neutro: { ...ponto, backgroundColor: c.neutro },
    positivo: { ...ponto, backgroundColor: c.positivo },
    meta,
    forte: { ...meta, fontFamily: FONTES.forte, color: c.tinta },
    numero: { fontFamily: FONTES.numero, fontSize: TAMANHO.texto, color: c.tinta, fontVariant: ['tabular-nums'] },
  }
})
