import { memo } from 'react'
import { Text, View } from 'react-native'
import Svg, { Path } from 'react-native-svg'

import { DADOS, MUNICIPIO_POR_ID } from '@/dados'
import { criarEstilos, FONTES, TAMANHO, useCores } from '@/tema'

/** Índice da faixa de cor (0 a 3), ou -1 para zero. */
export function faixa(valor: number, limites: number[]): number {
  let indice = -1
  limites.forEach((limite, i) => {
    if (valor >= limite) indice = i
  })
  return indice
}

/** [1, 3, 6, 12] → ["1–2", "3–5", "6–11", "12+"] */
function rotulosFaixas(limites: number[]): string[] {
  return limites.map((limite, i) => {
    const proximo = limites[i + 1]
    if (proximo == null) return `${limite}+`
    return proximo - 1 === limite ? `${limite}` : `${limite}–${proximo - 1}`
  })
}

type Props = {
  valores: Map<number, number>
  limites: number[]
  selecionado: number | null
  onSelecionar: (id: number) => void
}

export const Mapa = memo(function Mapa({ valores, limites, selecionado, onSelecionar }: Props) {
  const c = useCores()
  const s = useEstilos()
  const { largura, altura } = DADOS.mapa
  const escolhido = selecionado == null ? undefined : MUNICIPIO_POR_ID.get(selecionado)
  return (
    <View style={s.coluna}>
      <View style={[s.caixa, { aspectRatio: largura / altura }]}>
        <Svg width="100%" height="100%" viewBox={`0 0 ${largura} ${altura}`}>
          {DADOS.municipios.map((m) => {
            const indice = faixa(valores.get(m.id) ?? 0, limites)
            return (
              <Path
                key={m.id}
                d={m.d}
                fill={indice < 0 ? c.vazio : c.rampa[indice]}
                stroke={c.tela}
                strokeWidth={0.6}
                strokeLinejoin="round"
                onPress={() => onSelecionar(m.id)}
              />
            )
          })}
          {escolhido ? (
            <Path d={escolhido.d} fill="none" stroke={c.tinta} strokeWidth={1.6} strokeLinejoin="round" />
          ) : null}
        </Svg>
      </View>
      <View style={s.legenda}>
        {rotulosFaixas(limites).map((rotulo, i) => (
          <View key={rotulo} style={s.item}>
            <View style={[s.amostra, { backgroundColor: c.rampa[i] }]} />
            <Text style={s.rotulo}>{rotulo}</Text>
          </View>
        ))}
      </View>
    </View>
  )
})

const useEstilos = criarEstilos((c) => ({
  coluna: { gap: 10 },
  caixa: { width: '100%' },
  legenda: { flexDirection: 'row', flexWrap: 'wrap', columnGap: 10, rowGap: 4 },
  item: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  amostra: { width: 10, height: 10, borderRadius: 2 },
  rotulo: { fontFamily: FONTES.texto, fontSize: TAMANHO.meta, color: c.tinta2, fontVariant: ['tabular-nums'] },
}))
