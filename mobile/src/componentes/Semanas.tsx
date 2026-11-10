import { memo } from 'react'
import { Text, View } from 'react-native'

import { LinhaSemana, ROTULO_SENTIMENTO, SENTIMENTOS } from '@/dados'
import { dataCurta } from '@/formato'
import { criarEstilos, FONTES, TAMANHO, useCores } from '@/tema'

const ALTURA = 96

type Props = { linhas: LinhaSemana[] }

/** Colunas por semana, empilhadas por sentimento (negativo embaixo). */
export const Semanas = memo(function Semanas({ linhas }: Props) {
  const c = useCores()
  const s = useEstilos()
  const maximo = Math.max(1, ...linhas.map((l) => l.total))
  return (
    <View style={s.bloco}>
      <Text style={s.titulo}>Por semana</Text>
      <View style={s.grafico}>
        {linhas.map((l, i) => (
          <View
            key={l.inicio}
            style={s.coluna}
            accessible
            accessibilityLabel={`Semana de ${dataCurta(l.inicio)}: ${l.total} menções, ${SENTIMENTOS.map(
              (st) => `${l[st]} ${ROTULO_SENTIMENTO[st].toLowerCase()}`,
            ).join(', ')}`}
          >
            <Text style={s.total}>{l.total > 0 ? l.total : ''}</Text>
            <View style={[s.pilha, { height: (ALTURA * l.total) / maximo }]}>
              {SENTIMENTOS.filter((st) => l[st] > 0).map((st) => (
                <View key={st} style={[s.segmento, { flex: l[st], backgroundColor: c[st] }]} />
              ))}
            </View>
            <View style={s.base} />
            {/* Rótulo a cada duas semanas, a partir da mais recente, para não amontoar. */}
            <Text style={s.data}>{(linhas.length - 1 - i) % 2 === 0 ? dataCurta(l.inicio) : ''}</Text>
          </View>
        ))}
      </View>
    </View>
  )
})

const useEstilos = criarEstilos((c) => ({
  bloco: { gap: 12 },
  titulo: { fontFamily: FONTES.forte, fontSize: TAMANHO.texto, color: c.tinta },
  grafico: { flexDirection: 'row', alignItems: 'flex-end', gap: 6 },
  coluna: { flex: 1, alignItems: 'stretch', gap: 4 },
  total: {
    textAlign: 'center',
    fontFamily: FONTES.numero,
    fontSize: TAMANHO.meta,
    color: c.tinta,
    fontVariant: ['tabular-nums'],
  },
  // Colunas empilhadas com 2 px de vão e pontas arredondadas.
  pilha: { flexDirection: 'column-reverse', gap: 2 },
  segmento: { borderRadius: 3, minHeight: 3 },
  base: { height: 1, backgroundColor: c.linha },
  data: {
    textAlign: 'center',
    fontFamily: FONTES.texto,
    fontSize: TAMANHO.eixo,
    color: c.tinta3,
    fontVariant: ['tabular-nums'],
  },
}))
