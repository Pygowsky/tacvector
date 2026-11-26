import type { Ability, Agent, FiltersResponse, MapInfo, Player, Team } from './types'

export interface Lookups {
  ability: Map<string, Ability>
  agent: Map<string, Agent>
  player: Map<string, Player>
  team: Map<string, Team>
  map: Map<string, MapInfo>
  /** map slug -> callout label -> minimap position */
  calloutUV: Map<string, Map<string, [number, number]>>
}

export function buildLookups(f: FiltersResponse): Lookups {
  const calloutUV = new Map<string, Map<string, [number, number]>>()
  for (const m of f.maps) {
    calloutUV.set(m.slug, new Map(m.callouts.filter((c) => c.label).map((c) => [c.label as string, [c.u, c.v]])))
  }
  return {
    ability: new Map(f.ability_categories.flatMap((c) => c.abilities.map((a) => [a.ability_id, a]))),
    agent: new Map(f.agents.map((a) => [a.slug, a])),
    player: new Map(f.players.map((p) => [p.player_id, p])),
    team: new Map(f.teams.map((t) => [t.team_id, t])),
    map: new Map(f.maps.map((m) => [m.slug, m])),
    calloutUV,
  }
}
