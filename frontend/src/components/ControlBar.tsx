import { useMemo } from 'react'
import type { Selection } from '../selection'
import type { AbilityCategoryName, BuyTier, FiltersResponse, Side, WeaponSlug } from '../types'
import { Combobox, type Option } from './Combobox'
import { Toggle } from './ui'

interface Props {
  filters: FiltersResponse
  sel: Selection
  update: (patch: Partial<Selection>, opts?: { pinMap?: boolean }) => void
  /** Team-rounds per map for the selected team (undefined while loading or with no team). */
  teamMapRounds: Record<string, number> | undefined
}

const fmtDate = (iso: string) => iso.slice(0, 10)

export function ControlBar({ filters, sel, update, teamMapRounds }: Props) {
  const agentName = useMemo(() => Object.fromEntries(filters.agents.map((a) => [a.slug, a.name])), [filters])

  const teamOptions: Option[] = useMemo(
    () => filters.teams.map((t) => ({
      value: t.team_id,
      label: t.name,
      sublabel: `${t.games} games · last played ${fmtDate(t.last_played)}`,
      meta: t.is_cloud9 ? 'C9' : undefined,
    })),
    [filters],
  )

  const mapOptions: Option[] = useMemo(() => {
    const opts = filters.maps.map((m) => {
      const rounds = teamMapRounds?.[m.slug] ?? 0
      return {
        value: m.slug,
        label: m.name,
        icon: m.minimap_url,
        meta: teamMapRounds ? (rounds ? `${rounds} rds` : 'no data') : `${m.games} games`,
        dim: !!teamMapRounds && !rounds,
        rounds: teamMapRounds ? rounds : m.games,
      }
    })
    return opts.sort((a, b) => b.rounds - a.rounds)
  }, [filters, teamMapRounds])

  const playerOptions: Option[] = useMemo(() => {
    const pool = sel.team ? filters.players.filter((p) => p.team_id === sel.team) : filters.players
    return [...pool]
      .sort((a, b) => b.games - a.games)
      .map((p) => ({
        value: p.player_id,
        label: p.name,
        sublabel: [sel.team ? null : p.team_name, p.top_agents.map((a) => agentName[a] ?? a).join(', ')]
          .filter(Boolean).join(' · '),
        meta: `${p.games} g`,
      }))
  }, [filters, sel.team, agentName])

  const agentOptions: Option[] = useMemo(
    () => filters.agents.map((a) => ({ value: a.slug, label: a.name, sublabel: a.role ?? undefined, icon: a.display_icon })),
    [filters],
  )

  const sideOptions: Option[] = [
    { value: 'attacker', label: 'Attack' },
    { value: 'defender', label: 'Defense' },
  ]
  const tierOptions: Option[] = filters.buy_tiers.map((t) => ({ value: t.id, label: t.id, sublabel: t.definition }))
  const categoryOptions: Option[] = filters.ability_categories.map((c) => ({
    value: c.category,
    label: c.category,
    sublabel: c.abilities.slice(0, 3).map((a) => a.name).join(', '),
    meta: `${c.abilities.length}`,
  }))
  const weaponOptions: Option[] = filters.high_impact_weapons.map((w) => ({
    value: w.slug, label: w.name, sublabel: `${w.category} · ${w.cost.toLocaleString()} cr`, icon: w.display_icon,
  }))

  const presets = filters.self_scout_presets
  const hasOptional = sel.player || sel.agent || sel.category || sel.side || sel.buyTier

  return (
    <div className="controls">
      <div className="controls-row">
        <Combobox
          label="Team"
          value={sel.team}
          options={teamOptions}
          clearLabel="All teams"
          width={230}
          searchable
          onChange={(team) => update({ team, player: null })}
          header={(close) =>
            presets.length > 0 && (
              <div className="preset-chips">
                <span className="preset-title">Self-Scout presets</span>
                <div className="chips">
                  {presets.map((p) => (
                    <button key={p.team} type="button"
                      className={`chip${sel.team === p.team && sel.mode === 'self' ? ' on' : ''}`}
                      onClick={() => {
                        update({ team: p.team, mode: 'self', player: null })
                        close()
                      }}>
                      {p.label.replace(/^Self-Scout:\s*/, '')}
                    </button>
                  ))}
                </div>
              </div>
            )}
        />
        <div className="ctl">
          <span className="cb-label">Mode</span>
          <Toggle
            ariaLabel="Scouting mode"
            value={sel.mode}
            onChange={(mode) => update({ mode })}
            options={[
              { value: 'opponent', label: 'Opponent Scout', title: 'Read the selected team as an opponent: tendencies and how to beat them' },
              { value: 'self', label: 'Self-Scout', title: 'Read the selected team as your own: what opponents can predict and exploit' },
            ]}
          />
        </div>
        <span className="divider" aria-hidden="true" />
        <Combobox label="Map" value={sel.map} options={mapOptions} width={180} searchable
          onChange={(map) => map && update({ map }, { pinMap: true })} />
        <Combobox label="Side" value={sel.side} options={sideOptions} clearLabel="All sides" width={130}
          onChange={(v) => update({ side: v as Side | null })} />
        <Combobox label="Buy tier" value={sel.buyTier} options={tierOptions} clearLabel="All buys" width={150}
          onChange={(v) => update({ buyTier: v as BuyTier | null })} />
      </div>
      <div className="controls-row secondary">
        <span className="row-label">Optional</span>
        <Combobox label="Player" value={sel.player} options={playerOptions} clearLabel="Any player" width={190} searchable
          onChange={(player) => update({ player })} />
        <Combobox label="Agent" value={sel.agent} options={agentOptions} clearLabel="Any agent" width={170} searchable
          onChange={(agent) => update({ agent })} />
        <Combobox label="Ability category" value={sel.category} options={categoryOptions} clearLabel="All utility"
          width={170} disabled={sel.lens !== 'utility'} disabledReason="Applies to the Utility Placement lens"
          onChange={(v) => update({ category: v as AbilityCategoryName | null })} />
        <Combobox label="Weapon" value={sel.weapon} options={weaponOptions} width={160}
          disabled={sel.lens !== 'operator'} disabledReason="Applies to the Operator & Odin lens"
          onChange={(v) => v && update({ weapon: v as WeaponSlug })} />
        {hasOptional && (
          <button type="button" className="btn ghost sm"
            onClick={() => update({ player: null, agent: null, category: null, side: null, buyTier: null })}>
            Clear filters
          </button>
        )}
      </div>
    </div>
  )
}
