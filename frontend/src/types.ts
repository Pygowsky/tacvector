// Response shapes for the TacVector FastAPI backend (backend/main.py).

/** `frac()` block: n of N with a preformatted fraction and percentage. */
export interface Frac {
  n: number
  of: number
  fraction: string
  pct: number | null
}

export type Phase = 'buy_phase' | '0-20s' | '20-40s' | '40s+'
export type InRoundPhase = Exclude<Phase, 'buy_phase'>
export type Side = 'attacker' | 'defender'
export type BuyTier = 'Pistol' | 'Eco' | 'Force' | 'Bonus' | 'Full Buy'
export type AbilityCategoryName = 'Placeable' | 'Recon' | 'Smoke' | 'Stall' | 'Flash' | 'Other'
export type WeaponSlug = 'operator' | 'odin'

// --------------------------------------------------------------------------- /api/filters

export interface Team {
  team_id: string
  name: string
  games: number
  last_played: string
  is_cloud9: boolean
}

export interface SelfScoutPreset {
  label: string
  team: string
  params: Record<string, string>
}

export interface Callout {
  label: string | null
  callout: string
  callout_super: string | null
  u: number
  v: number
}

export interface MapInfo {
  slug: string
  name: string
  minimap_url: string | null
  splash_url: string | null
  games: number
  callouts: Callout[]
  transform: {
    x_multiplier: number
    y_multiplier: number
    x_scalar_to_add: number
    y_scalar_to_add: number
    formula: string
  }
}

export interface Player {
  player_id: string
  name: string
  games: number
  team_id: string
  team_name: string | null
  last_played: string
  top_agents: string[]
}

export interface Agent {
  slug: string
  name: string
  role: string | null
  display_icon: string | null
  killfeed_icon: string | null
}

export interface Ability {
  ability_id: string
  name: string
  agent: string
  agent_name: string
  slot: string | null
  icon: string | null
  casts: number
  category: AbilityCategoryName
}

export interface AbilityCategory {
  category: AbilityCategoryName
  priority: number | null
  abilities: Ability[]
}

export interface Weapon {
  slug: WeaponSlug
  name: string
  category: string
  cost: number
  display_icon: string | null
  kill_icon: string | null
}

export interface FiltersResponse {
  teams: Team[]
  self_scout_presets: SelfScoutPreset[]
  maps: MapInfo[]
  players: Player[]
  agents: Agent[]
  ability_categories: AbilityCategory[]
  high_impact_weapons: Weapon[]
  buy_tiers: { id: BuyTier; definition: string }[]
  sides: Side[]
  round_phases: { id: Phase; definition: string }[]
}

// --------------------------------------------------------------------------- shared

export interface Scope {
  team: { id: string; name: string } | null
  player: { id: string; name: string } | null
  map: { slug: string; name: string } | null
  agent: string | null
  side: Side | null
  buy_tier: BuyTier | null
  excludes: string
}

export interface CalloutRef {
  map: string
  callout: string | null
  callout_super: string | null
  label: string | null
  u: number | null
  v: number | null
  rounds: Frac
}

// --------------------------------------------------------------------------- /api/utility

export interface CalloutDensity extends CalloutRef {
  casts: number
  rounds_by_category: Partial<Record<AbilityCategoryName, number>>
}

export interface CalloutCategory extends CalloutRef {
  category: AbilityCategoryName
  casts: number
  median_time_s: number | null
  buy_phase_share: number
  top_abilities: { ability_id: string; label: string; casts: number }[]
}

export interface RoundPhaseStat {
  phase: Phase
  casts: number
  share_of_casts: Frac
  rounds: Frac
  by_category: Partial<Record<AbilityCategoryName, number>>
  top_callouts: CalloutRef[]
}

export interface Combo {
  map: string
  region: string | null
  abilities: { ability_id: string; agent: string; label: string; category: AbilityCategoryName }[]
  label: string
  rounds: Frac
  median_start_s: number | null
}

export interface AntiStrat {
  rule: string
  category: string
  map?: string
  callout?: string | null
  recommendation: string
  evidence: Frac
  confidence: 'high' | 'medium' | 'low'
}

export interface UtilityPoint {
  u: number
  v: number
  aim_uv: [number, number] | null
  view: [number, number, number] | null
  ability_id: string
  category: AbilityCategoryName
  agent: string
  player_id: string
  callout: string | null
  time_s: number | null
  phase: Phase | null
  side: Side
  buy_tier: BuyTier | null
  map: string
  game_id: string
  round_number: number
}

export interface UtilityResponse {
  scope: Scope & { ability_category: AbilityCategoryName | null; ability: string | null }
  sample: {
    team_rounds: number
    team_rounds_by_map: Record<string, number>
    rounds_with_matching_utility: Frac
    casts: number
    points_returned: number
    point_sample_step: number | null
  }
  categories: { category: AbilityCategoryName; casts: number; rounds: Frac }[]
  callout_density: CalloutDensity[]
  callout_categories: CalloutCategory[]
  round_phases: RoundPhaseStat[]
  combos: Combo[]
  insights: string[]
  anti_strats: AntiStrat[]
  points: UtilityPoint[]
}

// --------------------------------------------------------------------------- /api/operator

export interface RateBlock {
  team_rounds: number
  possession: Frac
  bought: Frac
  round_win_with: Frac
  round_win_without: Frac
}

export interface WeaponPlayer {
  player_id: string
  name: string
  rounds_played: number
  possession: Frac
  acquired: { bought_self: number; received_drop: number; carried_over: number }
  bought_for_teammates: number
  by_side: Partial<Record<Side, Frac>>
}

export interface SetupPhase {
  phase: Phase
  rounds_with_position: Frac
  top_callouts: { map: string; callout: string; rounds: Frac }[]
}

export interface Angle {
  map: string
  from_callout: string | null
  to_callout: string | null
  kills: number
  median_time_s: number | null
  by_phase: Record<InRoundPhase, number>
  example: { from_uv: [number, number]; to_uv: [number, number] }
}

export interface OpeningDuel {
  map: string
  callout: string | null
  side: Side
  won: number
  lost: number
  win_rate: Frac
}

export type PositionKind = 'kill_with_operator' | 'kill_with_odin' | 'kill_other_weapon' | 'death' | 'utility_cast'

export interface WeaponPosition {
  kind: PositionKind
  player_id: string
  u: number
  v: number
  aim_uv: [number, number] | null
  callout: string | null
  time_s: number | null
  phase: Phase | null
  side: Side
  map: string
  game_id: string
  round_number: number
}

export interface OperatorResponse {
  scope: Scope & { weapon: WeaponSlug }
  weapon: Weapon | null
  sample: { team_rounds: number; holder_team_rounds: number }
  rates: {
    overall: RateBlock
    by_side: Record<Side, RateBlock>
    by_buy_tier: Partial<Record<BuyTier, RateBlock>>
    by_map: Record<string, RateBlock>
  }
  players: WeaponPlayer[]
  agents: { agent: string; name: string; possession: Frac }[]
  possession_shifts: {
    round_to_round: { from_player: string; to_player: string; from_id: string; to_id: string; times: number }[]
    drops: { buyer: string; recipient: string; buyer_id: string; recipient_id: string; times: number }[]
  }
  setups: Partial<Record<Side, { holder_rounds: number; phases: SetupPhase[] }>>
  angles: Angle[]
  weapon_kills_by_phase: Record<InRoundPhase, number>
  opening_duels: {
    total: { won: number; lost: number; win_rate: Frac }
    by_callout: OpeningDuel[]
  }
  positions: WeaponPosition[]
  summaries: string[]
}
