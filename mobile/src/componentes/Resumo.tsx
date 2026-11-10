import { Pressable, Text, View } from 'react-native'

import { criarEstilos, FONTES, TAMANHO } from '@/tema'

export type Numero = { valor: string; rotulo: string; cor?: string }

type Props = {
  principal: Numero
  numeros: Numero[]
  municipio?: string
  onLimpar: () => void
  rodape: string | null
}

/** Coluna ao lado do mapa: o número principal do recorte, os demais em linhas e, se houver, o município escolhido. */
export function Resumo({ principal, numeros, municipio, onLimpar, rodape }: Props) {
  const s = useEstilos()
  return (
    <View style={s.coluna}>
      {municipio ? (
        <View style={s.municipio}>
          <Text style={s.nome}>{municipio}</Text>
          <Pressable onPress={onLimpar} hitSlop={8} accessibilityRole="button">
            <Text style={s.limpar}>Ver o estado todo</Text>
          </Pressable>
        </View>
      ) : null}
      <View>
        <Text style={s.principal}>{principal.valor}</Text>
        <Text style={s.rotulo}>{principal.rotulo}</Text>
      </View>
      <View style={s.lista}>
        {numeros.map((n) => (
          <View key={n.rotulo} style={s.linha}>
            <Text style={s.valor}>{n.valor}</Text>
            <View style={s.rotuloComMarca}>
              {n.cor ? <View style={[s.marca, { backgroundColor: n.cor }]} /> : null}
              <Text style={s.rotulo}>{n.rotulo}</Text>
            </View>
          </View>
        ))}
      </View>
      {rodape ? <Text style={s.rodape}>{rodape}</Text> : null}
    </View>
  )
}

const useEstilos = criarEstilos((c) => {
  const rotulo = { fontFamily: FONTES.texto, fontSize: TAMANHO.meta, lineHeight: 17, color: c.tinta2 } as const
  return {
    coluna: { flex: 1, gap: 18, paddingTop: 4 },
    municipio: { gap: 2 },
    nome: { fontFamily: FONTES.forte, fontSize: TAMANHO.aba, color: c.tinta },
    limpar: { fontFamily: FONTES.medio, fontSize: TAMANHO.meta, color: c.destaque },
    principal: {
      fontFamily: FONTES.numero,
      fontSize: TAMANHO.destaque,
      lineHeight: 44,
      color: c.tinta,
      fontVariant: ['tabular-nums'],
    },
    rotulo,
    lista: { gap: 10, paddingTop: 14, borderTopWidth: 1, borderTopColor: c.linha },
    linha: { gap: 0 },
    valor: { fontFamily: FONTES.numero, fontSize: TAMANHO.numero, color: c.tinta, fontVariant: ['tabular-nums'] },
    rotuloComMarca: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    marca: { width: 8, height: 8, borderRadius: 4 },
    rodape: { ...rotulo, color: c.tinta3 },
  }
})
