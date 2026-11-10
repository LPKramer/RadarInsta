import { memo } from 'react'
import { Linking, Pressable, Text, View } from 'react-native'

import { ROTULO_TIPO } from '@/dados'
import { compacto } from '@/formato'
import { criarEstilos, FONTES, MARGEM, TAMANHO } from '@/tema'

type Props = {
  username: string
  nome: string | null
  tipo: string
  seguidores: number | null
  municipios: string
}

/** Uma conta monitorada. Tocar abre o perfil no Instagram. */
export const ItemConta = memo(function ItemConta({ username, nome, tipo, seguidores, municipios }: Props) {
  const s = useEstilos()
  return (
    <Pressable
      style={({ pressed }) => (pressed ? s.itemPressionado : s.item)}
      onPress={() => Linking.openURL(`https://www.instagram.com/${username}/`)}
      accessibilityRole="link"
    >
      <View style={s.corpo}>
        <View style={s.topo}>
          <Text style={s.username} numberOfLines={1}>
            @{username}
          </Text>
          {seguidores != null ? <Text style={s.numero}>{compacto(seguidores)}</Text> : null}
        </View>
        {nome ? (
          <Text style={s.nome} numberOfLines={1}>
            {nome}
          </Text>
        ) : null}
        <View style={s.etiquetas}>
          <Text style={s.forte}>{ROTULO_TIPO[tipo] ?? tipo}</Text>
          {municipios ? (
            <Text style={s.meta} numberOfLines={1}>
              {municipios}
            </Text>
          ) : null}
        </View>
      </View>
    </Pressable>
  )
})

const useEstilos = criarEstilos((c) => {
  const item = { paddingLeft: MARGEM, backgroundColor: c.tela } as const
  const meta = { fontFamily: FONTES.texto, fontSize: TAMANHO.meta, color: c.tinta2, flexShrink: 1 } as const
  return {
    item,
    itemPressionado: { ...item, backgroundColor: c.campo },
    corpo: { gap: 4, paddingVertical: 14, paddingRight: MARGEM, borderBottomWidth: 1, borderBottomColor: c.linha },
    topo: { flexDirection: 'row', alignItems: 'baseline', gap: 12 },
    username: { flex: 1, fontFamily: FONTES.forte, fontSize: TAMANHO.texto, color: c.tinta },
    numero: { fontFamily: FONTES.numero, fontSize: TAMANHO.texto, color: c.tinta, fontVariant: ['tabular-nums'] },
    nome: { ...meta, color: c.tinta },
    etiquetas: { flexDirection: 'row', alignItems: 'baseline', columnGap: 12 },
    meta,
    forte: { ...meta, fontFamily: FONTES.medio, color: c.tinta2, flexShrink: 0 },
  }
})
