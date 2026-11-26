import type { Params } from './api'
import type { AbilityCategoryName, BuyTier, Side, WeaponSlug } from './types'

export type Mode = 'opponent' | 'self'
export type Lens = 'utility' | 'operator'

export interface Selection {
  team: string | null
  mode: Mode
  map: string | null
  side: Side | null
  buyTier: BuyTier | null
  player: string | null
  agent: string | null
  category: AbilityCategoryName | null
  weapon: WeaponSlug
  lens: Lens
}

export const EMPTY: Selection = {
  team: null, mode: 'opponent', map: null, side: null, buyTier: null,
  player: null, agent: null, category: null, weapon: 'operator', lens: 'utility',
}

// URL <-> selection, so a scouting view can be shared or bookmarked.
const URL_KEYS: Record<keyof Selection, string> = {
  team: 'team', mode: 'mode', map: 'map', side: 'side', buyTier: 'buy', player: 'player',
  agent: 'agent', category: 'cat', weapon: 'weapon', lens: 'lens',
}

export function readUrl(): { sel: Partial<Selection>; hasTeamParam: boolean } {
  const q = new URLSearchParams(window.location.search)
  const sel: Partial<Selection> = {}
  for (const [k, param] of Object.entries(URL_KEYS) as [keyof Selection, string][]) {
    const v = q.get(param)
    if (v) (sel as Record<string, string>)[k] = v
  }
  if (sel.mode && sel.mode !== 'self' && sel.mode !== 'opponent') delete sel.mode
  if (sel.lens && sel.lens !== 'utility' && sel.lens !== 'operator') delete sel.lens
  if (sel.weapon && sel.weapon !== 'operator' && sel.weapon !== 'odin') delete sel.weapon
  return { sel, hasTeamParam: q.has('team') }
}

export function writeUrl(sel: Selection) {
  const q = new URLSearchParams()
  for (const [k, param] of Object.entries(URL_KEYS) as [keyof Selection, string][]) {
    const v = sel[k]
    if (v !== null && v !== EMPTY[k]) q.set(param, String(v))
  }
  if (sel.team === null) q.set('team', '') // "All teams" is an explicit choice, not "use default"
  const s = q.toString()
  window.history.replaceState(null, '', s ? `?${s}` : window.location.pathname)
}

export function utilityParams(sel: Selection): Params | null {
  if (!sel.map) return null
  return {
    team: sel.team, map: sel.map, side: sel.side, player: sel.player, agent: sel.agent,
    ability_category: sel.category, buy_tier: sel.buyTier,
  }
}

export function operatorParams(sel: Selection): Params | null {
  if (!sel.map) return null
  return {
    team: sel.team, map: sel.map, side: sel.side, player: sel.player, agent: sel.agent,
    weapon: sel.weapon, buy_tier: sel.buyTier,
  }
}
