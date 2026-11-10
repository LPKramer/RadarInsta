import { memo } from 'react'
import { Pressable, Text, View } from 'react-native'

import { criarEstilos, FONTES, TAMANHO } from '@/tema'

type Opcao<T> = { valor: T; rotulo: string }

type PropsAbas<T> = {
  opcoes: Opcao<T>[]
  valor: T
  onMudar: (valor: T) => void
  pequenas?: boolean
}

/** Escolha única em texto, com um traço dourado sob a opção ativa. */
export function Abas<T extends string | number | null>({ opcoes, valor, onMudar, pequenas }: PropsAbas<T>) {
  const s = useEstilos()
  return (
    <View style={pequenas ? s.abasPequenas : s.abas} accessibilityRole="tablist">
      {opcoes.map((o) => {
        const ativa = o.valor === valor
        return (
          <Pressable
            key={o.rotulo}
            style={s.aba}
            onPress={() => onMudar(o.valor)}
            accessibilityRole="tab"
            accessibilityState={{ selected: ativa }}
            hitSlop={6}
          >
            <Text style={pequenas ? (ativa ? s.rotuloPequenoAtivo : s.rotuloPequeno) : ativa ? s.rotuloAtivo : s.rotulo}>
              {o.rotulo}
            </Text>
            <View style={ativa ? s.traco : s.tracoVazio} />
          </Pressable>
        )
      })}
    </View>
  )
}

type PropsChip = { rotulo: string; ativo: boolean; onPress: () => void }

/** Opção de múltipla escolha: contorno fino; preenchida quando ativa. */
export const Chip = memo(function Chip({ rotulo, ativo, onPress }: PropsChip) {
  const s = useEstilos()
  return (
    <Pressable
      style={ativo ? s.chipAtivo : s.chip}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityState={{ selected: ativo }}
      hitSlop={4}
    >
      <Text style={ativo ? s.chipRotuloAtivo : s.chipRotulo}>{rotulo}</Text>
    </Pressable>
  )
})

const useEstilos = criarEstilos((c) => {
  const rotulo = { fontFamily: FONTES.forte, fontSize: TAMANHO.aba, color: c.tinta3 } as const
  const rotuloPequeno = { fontFamily: FONTES.medio, fontSize: TAMANHO.texto, color: c.tinta2 } as const
  const traco = { height: 2, borderRadius: 1, alignSelf: 'stretch' } as const
  const chip = {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: c.linha,
  } as const
  const chipRotulo = { fontFamily: FONTES.medio, fontSize: TAMANHO.meta, color: c.tinta2 } as const
  return {
    abas: { flexDirection: 'row', gap: 24 },
    abasPequenas: { flexDirection: 'row', gap: 18 },
    aba: { gap: 6 },
    rotulo,
    rotuloAtivo: { ...rotulo, color: c.tinta },
    rotuloPequeno,
    rotuloPequenoAtivo: { ...rotuloPequeno, fontFamily: FONTES.forte, color: c.tinta },
    traco: { ...traco, backgroundColor: c.destaque },
    tracoVazio: traco,
    chip,
    chipAtivo: { ...chip, borderColor: c.tinta, backgroundColor: c.tinta },
    chipRotulo,
    chipRotuloAtivo: { ...chipRotulo, color: c.tela },
  }
})
