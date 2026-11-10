import { memo } from 'react'
import { Pressable, Text, View } from 'react-native'

import { MUNICIPIO_POR_ID } from '@/dados'
import { numero } from '@/formato'
import { criarEstilos, FONTES, TAMANHO, useCores } from '@/tema'

import { faixa } from './Mapa'

type Props = {
  linhas: { id: number; total: number }[]
  limites: number[]
  selecionado: number | null
  onSelecionar: (id: number) => void
}

/** Os municípios com mais itens, em barras na cor do mapa. É também a leitura do mapa em forma de tabela. */
export const Municipios = memo(function Municipios({ linhas, limites, selecionado, onSelecionar }: Props) {
  const c = useCores()
  const s = useEstilos()
  const maximo = Math.max(1, ...linhas.map((l) => l.total))
  return (
    <View style={s.bloco}>
      <Text style={s.titulo}>Municípios</Text>
      {linhas.map((l) => {
        const ativo = selecionado === l.id
        const nome = MUNICIPIO_POR_ID.get(l.id)?.nome ?? String(l.id)
        const indice = faixa(l.total, limites)
        return (
          <Pressable
            key={l.id}
            style={selecionado != null && !ativo ? s.linhaApagada : s.linha}
            onPress={() => onSelecionar(l.id)}
            accessibilityRole="button"
            accessibilityState={{ selected: ativo }}
            accessibilityLabel={`${nome}: ${l.total}`}
          >
            <Text style={ativo ? s.nomeAtivo : s.nome} numberOfLines={1}>
              {nome}
            </Text>
            <View style={s.trilho}>
              <View
                style={[
                  s.barra,
                  { width: `${(100 * l.total) / maximo}%`, backgroundColor: indice < 0 ? c.vazio : c.rampa[indice] },
                ]}
              />
            </View>
            <Text style={s.total}>{numero(l.total)}</Text>
          </Pressable>
        )
      })}
    </View>
  )
})

const useEstilos = criarEstilos((c) => {
  const linha = { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 30 } as const
  const nome = { flex: 1.3, fontFamily: FONTES.texto, fontSize: TAMANHO.texto, color: c.tinta } as const
  return {
    bloco: { gap: 4 },
    titulo: { fontFamily: FONTES.forte, fontSize: TAMANHO.texto, color: c.tinta, marginBottom: 8 },
    linha,
    linhaApagada: { ...linha, opacity: 0.4 },
    nome,
    nomeAtivo: { ...nome, fontFamily: FONTES.forte },
    trilho: { flex: 1, height: 6 },
    barra: { height: 6, borderRadius: 3 },
    total: {
      minWidth: 28,
      textAlign: 'right',
      fontFamily: FONTES.numero,
      fontSize: TAMANHO.texto,
      color: c.tinta,
      fontVariant: ['tabular-nums'],
    },
  }
})
