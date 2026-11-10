const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
const MESES_LONGOS = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
  'outubro', 'novembro', 'dezembro']

const inteiro = new Intl.NumberFormat('pt-BR')
const decimal = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 })
const percentual = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 })

/** "2026-11-01" → "1 nov" */
export function dataCurta(iso: string): string {
  const [, mes, dia] = iso.split('-')
  return `${Number(dia)} ${MESES[Number(mes) - 1]}`
}

/** "2026-11-01" → "1 de novembro de 2026" */
export function dataLonga(iso: string): string {
  const [ano, mes, dia] = iso.split('-')
  return `${Number(dia)} de ${MESES_LONGOS[Number(mes) - 1]} de ${ano}`
}

/** Data e hora locais de um instante ISO: "1 nov, 15:29" */
export function momento(iso: string): string {
  const d = new Date(iso)
  const hora = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return `${d.getDate()} ${MESES[d.getMonth()]}, ${hora}`
}

export function numero(n: number): string {
  return inteiro.format(n)
}

/** 56.000 → "56 mil"; 1.520 → "1,5 mil" */
export function compacto(n: number): string {
  if (n >= 1e6) return `${decimal.format(n / 1e6)} mi`
  if (n >= 1e4) return `${inteiro.format(Math.round(n / 1e3))} mil`
  if (n >= 1e3) return `${decimal.format(n / 1e3)} mil`
  return inteiro.format(n)
}

export function porcento(n: number): string {
  return `${percentual.format(n)}%`
}

export function vezes(n: number): string {
  return `${decimal.format(n)}×`
}
