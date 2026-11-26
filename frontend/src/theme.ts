import type { Frac, Phase } from './types'

/**
 * Round-timing encoding, used on the minimap, the legend, pills and bars alike.
 * In-round windows take the first three dark-mode categorical slots, which pass the
 * validator's all-pairs CVD and normal-vision checks on the panel surface (#15171c).
 * Buy-phase setups are deliberately neutral and drawn as a hollow diamond, so the
 * shape (not only the color) identifies them.
 */
export const PHASES: Phase[] = ['buy_phase', '0-20s', '20-40s', '40s+']

export const PHASE_META: Record<Phase, { label: string; short: string; color: string; hint: string }> = {
  buy_phase: { label: 'Buy phase', short: 'Buy', color: '#c3c2b7', hint: 'Set up before barriers drop' },
  '0-20s': { label: '0–20s', short: '0–20s', color: '#3987e5', hint: 'First 20 seconds after barriers drop' },
  '20-40s': { label: '20–40s', short: '20–40s', color: '#d95926', hint: '20 to 40 seconds in' },
  '40s+': { label: '40s+', short: '40s+', color: '#199e70', hint: '40 seconds or later' },
}

/** Acquisition split (bought / teammate drop / carried over). Validated as an adjacent set. */
export const ACQUIRE_META = {
  bought_self: { label: 'Bought', color: '#9085e9' },
  received_drop: { label: 'Teammate drop', color: '#d55181' },
  carried_over: { label: 'Carried over', color: '#c98500' },
} as const

export const STATUS = { good: '#0ca30c', critical: '#d03b3b', warning: '#fab219' }

export const SIDE_LABEL = { attacker: 'Attack', defender: 'Defense' } as const

/** Round half to even, like Python's format(), so numbers match the backend's insight text. */
export function roundEven(x: number, digits = 0): number {
  const k = 10 ** digits
  const v = x * k
  const r = Math.round(v)
  return (Math.abs(v % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r) / k
}

export const pct = (f: Frac | null | undefined, digits = 0): string =>
  f && f.pct !== null && f.of ? `${roundEven(f.pct, digits).toFixed(digits)}%` : '–'

/** "46% · 134/289" */
export const fracText = (f: Frac | null | undefined): string => (f ? `${pct(f)} · ${f.fraction}` : '–')

export const fmtTime = (s: number | null | undefined): string => {
  if (s === null || s === undefined) return '–'
  if (s < 0) return `${Math.abs(s).toFixed(0)}s before barriers`
  return `${s.toFixed(1)}s`
}
