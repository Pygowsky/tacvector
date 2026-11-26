import { Fragment, useMemo, useState, type ReactNode } from 'react'
import { useApi } from '../api'
import { Minimap, type MapBubble, type MapPoint } from '../components/Minimap'
import { PhasePills } from '../components/PhasePills'
import { Check, ConfidenceBadge, Empty, FracBar, Section } from '../components/ui'
import type { Lookups } from '../lookups'
import { utilityParams, type Selection } from '../selection'
import { fmtTime, fracText, pct, PHASE_META, PHASES, SIDE_LABEL } from '../theme'
import type { CalloutRef, Phase, UtilityPoint, UtilityResponse } from '../types'
import { ErrorNote, MapHeader } from './shared'

interface Props {
  sel: Selection
  lookups: Lookups
  nonce: number
  lensToggle: ReactNode
}

const MAX_BUBBLES = 14
const LABELLED_BUBBLES = 7

export function UtilityView({ sel, lookups, nonce, lensToggle }: Props) {
  const { data, error, loading } = useApi<UtilityResponse>('utility', utilityParams(sel), nonce)
  const [phases, setPhases] = useState<Set<Phase>>(() => new Set(PHASES))
  const [showDensity, setShowDensity] = useState(true)
  const [showAim, setShowAim] = useState(true)
  const [showNames, setShowNames] = useState(false)

  const map = sel.map ? lookups.map.get(sel.map) : undefined
  const self = sel.mode === 'self'

  const points = useMemo<MapPoint[]>(() => {
    if (!data) return []
    return data.points
      .filter((p) => p.phase && phases.has(p.phase))
      .map((p) => ({
        u: p.u,
        v: p.v,
        color: PHASE_META[p.phase ?? 'buy_phase'].color,
        shape: p.phase === 'buy_phase' ? 'diamond' : 'dot',
        aim: showAim ? p.aim_uv : null,
        tip: <CastTip p={p} lookups={lookups} />,
      }))
  }, [data, phases, showAim, lookups])

  // Callout frequency: with exactly one timing window selected, use that window's own n/N
  // (`round_phases[].top_callouts`), so the numbers on the map always match the filter.
  const singlePhase = phases.size === 1 ? [...phases][0] : null
  const bubbles = useMemo<MapBubble[]>(() => {
    if (!data || !showDensity) return []
    const source: CalloutRef[] = singlePhase
      ? data.round_phases.find((r) => r.phase === singlePhase)?.top_callouts ?? []
      : data.callout_density
    const located = source.filter((c) => c.u !== null && c.v !== null && c.label).slice(0, MAX_BUBBLES)
    const maxPct = Math.max(1, ...located.map((c) => c.rounds.pct ?? 0))
    return located.map((c, i) => ({
      key: `${c.callout_super}-${c.callout}`,
      u: c.u as number,
      v: c.v as number,
      weight: (c.rounds.pct ?? 0) / maxPct,
      title: c.label as string,
      sub: `${pct(c.rounds)} · ${c.rounds.fraction}`,
      showLabel: i < LABELLED_BUBBLES,
      tip: <DensityTip c={c} data={data} phase={singlePhase} />,
    }))
  }, [data, showDensity, singlePhase])

  const phaseCounts = useMemo(
    () => Object.fromEntries((data?.round_phases ?? []).map((p) => [p.phase, p.casts])) as Partial<Record<Phase, number>>,
    [data],
  )

  if (!map) return <main className="workspace"><Empty>Select a map.</Empty></main>

  const sample = data?.sample
  const noRounds = data && data.sample.team_rounds === 0

  return (
    <main className="workspace">
      {/* ---------------------------------------------------------------- left: breakdown */}
      <aside className="panel left" aria-label="Utility breakdown">
        <Section title="Sample" hint="Team-rounds: one team's half of one round. Corrupted games and rounds are excluded.">
          {sample ? (
            <div className="sample">
              <div><strong>{sample.team_rounds.toLocaleString()}</strong><span>team-rounds</span></div>
              <div><strong>{sample.casts.toLocaleString()}</strong><span>utility casts</span></div>
              <div><strong>{sample.rounds_with_matching_utility.fraction}</strong>
                <span>rounds with matching utility ({pct(sample.rounds_with_matching_utility)})</span></div>
            </div>
          ) : <Empty>Loading…</Empty>}
        </Section>
        <Section title="Utility mix" hint="Share of team-rounds in which each category was used at least once">
          {data?.categories.length ? data.categories.map((c) => (
            <FracBar key={c.category} label={<>{c.category} <small className="muted">{c.casts.toLocaleString()} casts</small></>}
              frac={c.rounds} />
          )) : <Empty>No utility in scope.</Empty>}
        </Section>
        <Section title="Round timing" hint="Share of all casts that land in each timing window; rounds = team-rounds with any cast in that window">
          {data?.round_phases.map((p) => (
            <FracBar key={p.phase} swatch color={PHASE_META[p.phase].color}
              label={PHASE_META[p.phase].label} frac={p.share_of_casts}
              title={`Used in ${fracText(p.rounds)} team-rounds`} />
          ))}
        </Section>
      </aside>

      {/* ---------------------------------------------------------------- center: minimap */}
      <section className="panel center" aria-label="Utility placement map">
        <MapHeader map={map} sel={sel} lookups={lookups} lensToggle={lensToggle} loading={loading} />
        <div className="map-toolbar">
          <PhasePills active={phases} onChange={setPhases} counts={phaseCounts} />
          <div className="layer-checks">
            <Check checked={showDensity} onChange={setShowDensity}>Callout frequency</Check>
            <Check checked={showAim} onChange={setShowAim}>Aim ticks</Check>
            <Check checked={showNames} onChange={setShowNames}>Callout names</Check>
          </div>
        </div>
        <div className="map-stage">
          <Minimap map={map} points={points} bubbles={bubbles} showCalloutNames={showNames} loading={loading && !data}
            empty={error ? <ErrorNote error={error} /> : noRounds ? 'No rounds match these filters.' : null} />
        </div>
        <p className="map-foot muted">
          {sample && sample.point_sample_step && sample.point_sample_step > 1
            ? `Plotting ${sample.points_returned.toLocaleString()} of ${sample.casts.toLocaleString()} casts (1 in every ${sample.point_sample_step}, evenly sampled). `
            : sample ? `Plotting all ${sample.points_returned.toLocaleString()} casts. ` : ''}
          Ticks show aim direction. Circles: share of team-rounds with utility at that callout
          {singlePhase ? ` during ${PHASE_META[singlePhase].label}` : ' (all timings)'}.
        </p>
      </section>

      {/* ---------------------------------------------------------------- right: insights */}
      <aside className="panel right" aria-label="Insights">
        <Section title={self ? 'Your tendencies' : 'Scouting report'}>
          {data?.insights.length ? (
            <ul className="cards">
              {data.insights.map((t, i) => <li key={i} className="card insight">{t}</li>)}
            </ul>
          ) : <Empty>{loading ? 'Loading…' : 'No insights for this scope.'}</Empty>}
        </Section>

        <Section title={self ? 'Your recurring combos' : 'Recurring utility combos'}
          hint="Two or more different abilities from different players within 3s of each other, seen in 3+ rounds">
          {data?.combos.length ? (
            <ul className="combos">
              {data.combos.map((c) => (
                <li key={c.label + c.region} className="combo">
                  <div className="combo-abilities">
                    {c.abilities.map((a) => {
                      const ab = lookups.ability.get(a.ability_id)
                      return (
                        <span key={a.ability_id} className="ability-chip" title={`${a.label} · ${a.category}`}>
                          {ab?.icon && <img src={ab.icon} alt="" />}
                          {ab?.name ?? a.label}
                        </span>
                      )
                    })}
                  </div>
                  <div className="combo-meta">
                    <span>{c.region ? `${c.region} · ` : ''}{c.median_start_s !== null ? `~${c.median_start_s}s in` : ''}</span>
                    <span className="num">{fracText(c.rounds)}</span>
                  </div>
                </li>
              ))}
            </ul>
          ) : <Empty>No combo repeats in 3+ rounds.</Empty>}
        </Section>

        <Section title={self ? 'How opponents will counter you' : 'Anti-strat counters'}
          hint="Rule-based counters generated from the hotspots, combos and timing above">
          {self && data?.anti_strats.length ? (
            <p className="muted small">These are the counters a prepared opponent would run against each pattern. Vary the
              placement or timing to stay unreadable.</p>
          ) : null}
          {data?.anti_strats.length ? (
            <ul className="cards">
              {data.anti_strats.map((a, i) => (
                <li key={i} className="card anti">
                  <div className="anti-head">
                    <span className="tag">{a.category}</span>
                    {a.callout && <span className="muted">{a.callout}</span>}
                    <ConfidenceBadge level={a.confidence} />
                  </div>
                  <p>{a.recommendation}</p>
                  <span className="evidence">Evidence: {fracText(a.evidence)} rounds</span>
                </li>
              ))}
            </ul>
          ) : <Empty>No pattern is strong enough for a counter (needs 3+ rounds and 25%+ share).</Empty>}
        </Section>
      </aside>
    </main>
  )
}

function CastTip({ p, lookups }: { p: UtilityPoint; lookups: Lookups }) {
  const ab = lookups.ability.get(p.ability_id)
  const agent = lookups.agent.get(p.agent)
  const player = lookups.player.get(p.player_id)
  return (
    <>
      <div className="tip-title">
        {ab?.icon && <img src={ab.icon} alt="" />}
        {ab?.name ?? p.ability_id}
        <span className="muted"> · {agent?.name ?? p.agent}</span>
      </div>
      <dl>
        <dt>Player</dt><dd>{player?.name ?? p.player_id}</dd>
        <dt>Callout</dt><dd>{p.callout ?? '–'}</dd>
        <dt>Timing</dt><dd>{p.phase ? PHASE_META[p.phase].label : '–'}{p.time_s !== null && p.time_s >= 0 ? ` (${fmtTime(p.time_s)})` : ''}</dd>
        <dt>Category</dt><dd>{p.category}</dd>
        <dt>Round</dt><dd>R{p.round_number} · {SIDE_LABEL[p.side]}{p.buy_tier ? ` · ${p.buy_tier}` : ''}</dd>
      </dl>
    </>
  )
}

function DensityTip({ c, data, phase }: { c: CalloutRef; data: UtilityResponse; phase: Phase | null }) {
  const full = data.callout_density.find((d) => d.callout === c.callout && d.callout_super === c.callout_super)
  return (
    <>
      <div className="tip-title">{c.label}</div>
      <dl>
        <dt>{phase ? PHASE_META[phase].label : 'Rounds'}</dt><dd>{fracText(c.rounds)}</dd>
        {full && <><dt>Casts</dt><dd>{full.casts}</dd></>}
        {full && Object.entries(full.rounds_by_category).map(([cat, n]) => (
          <Fragment key={cat}><dt>{cat}</dt><dd>{n}/{full.rounds.of} rds</dd></Fragment>
        ))}
      </dl>
    </>
  )
}
