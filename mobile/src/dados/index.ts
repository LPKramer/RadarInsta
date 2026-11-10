// O JSON é gerado pelo radar em Python: `python -m radar exportar-app`.
import bruto from './radar.json'

export type Sentimento = 'negativo' | 'neutro' | 'positivo'

export type Mencao = {
  id: string
  data: string
  conta: string
  rede?: string | null
  formato: string | null
  tipoConta: string | null
  seguidores: number | null
  tema: string
  setor: string | null
  sentimento: Sentimento
  alcance: string
  municipios: number[]
  citaEnergisa: boolean
  curtidas: number | null
  comentarios: number | null
  visualizacoes: number | null
  interacoes: number
  engajamentoPct: number | null
  indice: number | null
  repercutiram: number
  coberturaEstadual: boolean
  reacao: string | null
  reacaoResumo: string | null
  comentariosLidos: number | null
  resumo: string
  legenda: string | null
  link: string | null
}

export type Conta = {
  username: string
  nome: string | null
  tipo: string
  abrangencia: string | null
  prioridade: string | null
  score: number | null
  seguidores: number | null
  posts30d: number | null
  engajamentoPct: number | null
  municipios: number[]
}

export type Municipio = { id: number; nome: string; populacao: number | null; d: string }

type Dados = {
  geradoEm: string
  coletadoEm: string | null
  mapa: { largura: number; altura: number }
  municipios: Municipio[]
  mencoes: Mencao[]
  contas: Conta[]
}

export const DADOS = bruto as unknown as Dados
export const MUNICIPIO_POR_ID = new Map(DADOS.municipios.map((m) => [m.id, m]))
export const MENCAO_POR_ID = new Map(DADOS.mencoes.map((m) => [m.id, m]))
export const POPULACAO_TOTAL = DADOS.municipios.reduce((soma, m) => soma + (m.populacao ?? 0), 0)

export const SENTIMENTOS: Sentimento[] = ['negativo', 'neutro', 'positivo']

export const ROTULO_SENTIMENTO: Record<Sentimento, string> = {
  negativo: 'Negativo',
  neutro: 'Neutro',
  positivo: 'Positivo',
}

export const ROTULO_TEMA: Record<string, string> = {
  falta_energia: 'Falta de energia',
  desligamento_programado: 'Desligamento programado',
  conta_tarifa: 'Conta e tarifa',
  atendimento: 'Atendimento',
  obras_investimento: 'Obras e investimento',
  acidente_seguranca: 'Acidente e segurança',
  iluminacao_publica: 'Iluminação pública',
  institucional: 'Institucional',
  outro: 'Outro',
}

/** Área da Energisa mais envolvida no fato. */
export const ROTULO_SETOR: Record<string, string> = {
  operacao: 'Operação',
  manutencao: 'Manutenção',
  atendimento: 'Atendimento',
  comercial: 'Comercial',
  obras_expansao: 'Obras e expansão',
  seguranca: 'Segurança',
  rh: 'RH',
  institucional: 'Institucional',
  regulatorio_juridico: 'Regulatório e jurídico',
  nenhum: 'Fora da Energisa',
}

export const ROTULO_ALCANCE: Record<string, string> = {
  local: 'Local',
  regional: 'Regional',
  estadual: 'Estadual',
  nacional: 'Nacional',
}

export const ROTULO_TIPO: Record<string, string> = {
  portal_noticias: 'Portal',
  pagina_cidade_comunidade: 'Página da cidade',
  jornalista_radialista: 'Jornalista',
  perfil_fofoca: 'Fofoca',
  policial_plantao: 'Plantão policial',
  influenciador_local: 'Influenciador',
  orgao_publico: 'Órgão público',
  politico: 'Político',
  site_noticias: 'Site de notícia',
}

// Matéria de site: o nome do veículo, sem @.
export function nomeConta(m: Pick<Mencao, 'conta' | 'rede'>): string {
  return m.rede === 'web' ? m.conta : `@${m.conta}`
}

export const ROTULO_REACAO: Record<string, string> = {
  negativa: 'Comentários negativos',
  positiva: 'Comentários positivos',
  mista: 'Comentários divididos',
  neutra: 'Comentários neutros',
}

// Faixas de cor do mapa. Menções são poucas por município; contas chegam a dezenas em Palmas.
export const LIMITES_MENCOES = [1, 2, 3, 5]
export const LIMITES_CONTAS = [1, 3, 6, 12]

export type Periodo = 7 | 30 | 60 | null

export type FiltroMencoes = {
  periodo: Periodo
  sentimentos: Sentimento[]
  tema: string | null
  setor: string | null
  municipio: number | null
}

type Dimensao = 'tema' | 'setor' | 'municipio' | 'periodo'

/** Primeiro dia do período, contado a partir da data em que os dados foram gerados. */
function inicio(periodo: Periodo): string | null {
  if (periodo == null) return null
  const d = new Date(DADOS.geradoEm)
  d.setUTCDate(d.getUTCDate() - periodo + 1)
  return d.toISOString().slice(0, 10)
}

/** Aplica os filtros; `ignorar` deixa um deles de fora (cada gráfico não filtra pela própria dimensão). */
export function filtrarMencoes(filtro: FiltroMencoes, ignorar?: Dimensao): Mencao[] {
  const desde = ignorar === 'periodo' ? null : inicio(filtro.periodo)
  return DADOS.mencoes.filter(
    (m) =>
      (desde == null || m.data >= desde) &&
      (filtro.sentimentos.length === 0 || filtro.sentimentos.includes(m.sentimento)) &&
      (ignorar === 'tema' || filtro.tema == null || m.tema === filtro.tema) &&
      (ignorar === 'setor' || filtro.setor == null || m.setor === filtro.setor) &&
      (ignorar === 'municipio' || filtro.municipio == null || m.municipios.includes(filtro.municipio)),
  )
}

export function filtrarContas(tipos: string[], municipio: number | null): Conta[] {
  return DADOS.contas.filter(
    (c) => (tipos.length === 0 || tipos.includes(c.tipo)) && (municipio == null || c.municipios.includes(municipio)),
  )
}

export function contarPorMunicipio(itens: { municipios: number[] }[]): Map<number, number> {
  const contagem = new Map<number, number>()
  for (const item of itens) {
    for (const id of item.municipios) contagem.set(id, (contagem.get(id) ?? 0) + 1)
  }
  return contagem
}

/** Os municípios com mais itens, do maior para o menor. */
export function ranking(contagem: Map<number, number>, maximo: number): { id: number; total: number }[] {
  return [...contagem.entries()]
    .map(([id, total]) => ({ id, total }))
    .sort((a, b) => b.total - a.total)
    .slice(0, maximo)
}

export type LinhaGrupo = { chave: string; total: number } & Record<Sentimento, number>

/** Menções por tema ou por setor, divididas por sentimento, do grupo maior para o menor. */
export function agrupar(mencoes: Mencao[], campo: 'tema' | 'setor'): LinhaGrupo[] {
  const linhas = new Map<string, LinhaGrupo>()
  for (const m of mencoes) {
    const chave = m[campo]
    if (chave == null) continue
    const linha = linhas.get(chave) ?? { chave, total: 0, negativo: 0, neutro: 0, positivo: 0 }
    linha[m.sentimento] += 1
    linha.total += 1
    linhas.set(chave, linha)
  }
  return [...linhas.values()].sort((a, b) => b.total - a.total || b.negativo - a.negativo)
}

export type LinhaSemana = { inicio: string; total: number } & Record<Sentimento, number>

/** Menções por semana (de segunda a domingo), das `semanas` mais recentes até a data dos dados. */
export function porSemana(mencoes: Mencao[], semanas: number): LinhaSemana[] {
  const fim = new Date(DADOS.geradoEm.slice(0, 10) + 'T00:00:00Z')
  const segunda = new Date(fim)
  segunda.setUTCDate(fim.getUTCDate() - ((fim.getUTCDay() + 6) % 7))
  const linhas: LinhaSemana[] = []
  for (let i = semanas - 1; i >= 0; i--) {
    const d = new Date(segunda)
    d.setUTCDate(segunda.getUTCDate() - 7 * i)
    linhas.push({ inicio: d.toISOString().slice(0, 10), total: 0, negativo: 0, neutro: 0, positivo: 0 })
  }
  for (const m of mencoes) {
    // A última semana cujo início não passa da data do post.
    const linha = linhas.findLast((l) => l.inicio <= m.data)
    if (linha == null) continue
    linha[m.sentimento] += 1
    linha.total += 1
  }
  return linhas
}

export function municipiosSemConta(): Municipio[] {
  const cobertos = new Set(DADOS.contas.flatMap((c) => c.municipios))
  return DADOS.municipios.filter((m) => !cobertos.has(m.id))
}

export function nomesMunicipios(ids: number[]): string {
  return ids.map((id) => MUNICIPIO_POR_ID.get(id)?.nome ?? '').filter(Boolean).join(', ')
}

export function alternar<T>(lista: T[], valor: T): T[] {
  return lista.includes(valor) ? lista.filter((v) => v !== valor) : [...lista, valor]
}
