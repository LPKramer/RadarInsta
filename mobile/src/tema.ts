import { StyleSheet, useColorScheme } from 'react-native'

// Paletas validadas com o script da skill dataviz (contraste, separação para daltonismo, rampa monótona).
// Sentimento é divergente: vermelho e azul nos polos, cinza no meio. O dourado do capim do Jalapão pinta o mapa
// (do claro, poucos, ao escuro, muitos) e marca a opção escolhida; é a única cor de destaque da interface.
export const CORES = {
  light: {
    tela: '#FFFFFF',
    campo: '#F3F5F7',
    tinta: '#16202A',
    tinta2: '#5B6672',
    tinta3: '#8C96A0',
    linha: '#E4E8EB',
    vazio: '#E8EBEE',
    rampa: ['#D6AE57', '#BF8E33', '#9C6E1E', '#6E4C10'],
    destaque: '#B8862B',
    negativo: '#C8473A',
    neutro: '#B4BCC4',
    positivo: '#2F6FC4',
  },
  dark: {
    tela: '#12181E',
    campo: '#1B232B',
    tinta: '#E8ECEF',
    tinta2: '#A3ADB6',
    tinta3: '#76818B',
    linha: '#26303A',
    vazio: '#222B34',
    rampa: ['#735A2A', '#9A762F', '#C49638', '#EDC46A'],
    destaque: '#E0B456',
    negativo: '#E06A5A',
    neutro: '#56616B',
    positivo: '#5B95E0',
  },
}

export type Cores = (typeof CORES)['light']
export type Esquema = keyof typeof CORES

// Barlow nasceu da sinalização de rodovias: um tipo de infraestrutura, como a rede elétrica. A versão
// semicondensada fica com os números e os títulos.
export const FONTES = {
  texto: 'Barlow_400Regular',
  medio: 'Barlow_500Medium',
  forte: 'Barlow_600SemiBold',
  numero: 'BarlowSemiCondensed_600SemiBold',
}

// Escala curta: cada tamanho tem um papel só.
export const TAMANHO = { eixo: 11, meta: 13, texto: 15, aba: 17, numero: 22, destaque: 40 }

export const MARGEM = 20

export function useEsquema(): Esquema {
  return useColorScheme() === 'dark' ? 'dark' : 'light'
}

export function useCores(): Cores {
  return CORES[useEsquema()]
}

type Estilos = Parameters<typeof StyleSheet.create>[0]

/** Cria os estilos uma vez para cada esquema de cor e devolve o hook que escolhe o do momento. */
export function criarEstilos<T extends Estilos>(fabrica: (c: Cores) => T & Estilos) {
  const estilos = { light: StyleSheet.create(fabrica(CORES.light)), dark: StyleSheet.create(fabrica(CORES.dark)) }
  return function useEstilos() {
    return estilos[useEsquema()]
  }
}
