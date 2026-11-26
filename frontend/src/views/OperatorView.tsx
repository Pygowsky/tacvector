import { Fragment, useMemo, useState, type ReactNode } from 'react'
import { useApi } from '../api'
import { Minimap, type MapBubble, type MapPoint, type MapVector, type PointShape } from '../components/Minimap'
import { PhasePills } from '../components/PhasePills'
import { Check, Empty, FracBar, Legend, Section, SegBar, ShapeGlyph, StatTile, Toggle } from '../components/ui'
import type { Lookups } from '../lookups'
import { operatorParams, type Selection } from '../selection'
import { ACQUIRE_META, fmtTime, fracText, PHASE_META, PHASES, pct, SIDE_LABEL } from '../theme'
import type { InRoundPhase, OperatorResponse, Phase, PositionKind, Side, WeaponPosition } from '../types'
import { ErrorNote, MapHeader } from './shared'

interface Props {
  sel: Selection
  lookups: Lookups
  nonce: number
  lensToggle: ReactNode
}

type SideView = Side | 'both'
type KindGroup = 'kills' | 'deaths' | 'utility'

const SIGHTLINE = '#e8e6df'
const IN_ROUND: InRoundPhase[] = ['0-20s', '20-40s', '40s+']

function kindInfo(kind: PositionKind): { group: KindGroup; shape: PointShape; label: string } {
  if (kind === 'death') return { group: 'deaths', shape: 'cross', label: 'Death' }
  if (kind === 'utility_cast') return { group: 'utility', shape: 'small', label: 'Utility cast' }
  if (kind === 'kill_other_weapon') return { group: 'kills', shape: 'ring', label: 'Kill (other weapon)' }
  return { group: 'kills', shape: 'dot', label: 'Kill with weapon' }
}

export function OperatorView({ sel, lookups, nonce, lensToggle }: Props) {
  const { data, error, loading } = useApi<OperatorResponse>('operator', operatorParams(sel), nonce)
  const [phases, setPhases] = useState<Set<Phase>>(() => new Set(PHASES))
  const [sideView, setSideView] = useState<SideView>('defender')
  const [groups, setGroups] = useState<Record<KindGroup, boolean>>({ kills: true, deaths: true, utility: true })
  const [showSightlines, setShowSightlines] = useState(true)
  const [showDuels, setShowDuels] = useState(true)

  const map = sel.map ? lookups.map.get(sel.map) : undefined
  const self = sel.mode === 'self'
  const wname = data?.weapon?.name ?? (sel.weapon === 'odin' ? 'Odin' : 'Operator')
  // A global side filter wins over the local setup-side toggle.
  const side: SideView = sel.side ?? sideView
  const sideOk = (s: Side) => side === 'both' || s === side

  const points = useMemo<MapPoint[]>(() => {
    if (!data) return []
    return data.positions
      .filter((p) => sideOk(p.side) && p.phase && phases.has(p.phase) && groups[kindInfo(p.kind).group])
      .map((p) => ({
        u: p.u,
        v: p.v,
        color: PHASE_META[p.phase ?? 'buy_phase'].color,
        shape: kindInfo(p.kind).shape,
        aim: p.aim_uv,
        tip: <PositionTip p={p} lookups={lookups} wname={wname} />,
      }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, phases, groups, side, lookups, wname])

  const vectors = useMemo<MapVector[]>(() => {
    if (!data || !showSightlines) return []
    const maxKills = Math.max(1, ...data.angles.map((a) => a.kills))
    return data.angles
      .filter((a) => IN_ROUND.some((ph) => phases.has(ph) && a.by_phase[ph] > 0))
      .map((a) => ({
        from: a.example.from_uv,
        to: a.example.to_uv,
        color: SIGHTLINE,
        width: 1.5 + 2.5 * (a.kills / maxKills),
        tip: (
          <>
            <div className="tip-title">{a.from_callout ?? '?'} → {a.to_callout ?? '?'}</div>
            <dl>
              <dt>{wname} kills</dt><dd>{a.kills}</dd>
              <dt>Median time</dt><dd>{fmtTime(a.median_time_s)}</dd>
              {IN_ROUND.map((ph) => <Fragment key={ph}><dt>{PHASE_META[ph].label}</dt><dd>{a.by_phase[ph]}</dd></Fragment>)}
            </dl>
          </>
        ),
      }))
  }, [data, showSightlines, phases, wname])

  const bubbles = useMemo<MapBubble[]>(() => {
    if (!data || !showDuels || !sel.map) return []
    const uv = lookups.calloutUV.get(sel.map)
    const duels = data.opening_duels.by_callout.filter((d) => sideOk(d.side) && d.callout && uv?.has(d.callout))
    const maxN = Math.max(1, ...duels.map((d) => d.won + d.lost))
    return duels.map((d, i) => {
      const [u, v] = uv!.get(d.callout!)!
      return {
        key: `${d.callout}-${d.side}`,
        u, v,
        weight: (d.won + d.lost) / maxN,
        title: d.callout!,
        sub: `${d.won}W–${d.lost}L`,
        tone: (d.win_rate.pct ?? 0) >= 50 ? 'good' : 'bad',
        showLabel: i < 8,
        tip: (
          <>
            <div className="tip-title">Opening duels at {d.callout}</div>
            <dl>
              <dt>Record</dt><dd>{d.won}W – {d.lost}L ({SIDE_LABEL[d.side]})</dd>
              <dt>Win rate</dt><dd>{fracText(d.win_rate)}</dd>
            </dl>
          </>
        ),
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, showDuels, side, sel.map, lookups])

  const posCounts = useMemo(() => {
    const c: Partial<Record<Phase, number>> = {}
    for (const p of data?.positions ?? []) {
      if (p.phase && sideOk(p.side) && groups[kindInfo(p.kind).group]) c[p.phase] = (c[p.phase] ?? 0) + 1
    }
    return c
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, side, groups])

  if (!map) return <main className="workspace"><Empty>Select a map.</Empty></main>

  const o = data?.rates.overall
  const acq = (data?.players ?? []).reduce(
    (s, p) => ({
      bought_self: s.bought_self + p.acquired.bought_self,
      received_drop: s.received_drop + p.acquired.received_drop,
      carried_over: s.carried_over + p.acquired.carried_over,
    }),
    { bought_self: 0, received_drop: 0, carried_over: 0 },
  )
  const acqTotal = acq.bought_self + acq.received_drop + acq.carried_over
  const acqParts = (Object.keys(ACQUIRE_META) as (keyof typeof ACQUIRE_META)[]).map((k) => ({
    key: k, label: ACQUIRE_META[k].label, color: ACQUIRE_META[k].color, value: acq[k],
  }))
  const setupSides: Side[] = side === 'both' ? ['defender', 'attacker'] : [side]
  const noRounds = data && data.sample.team_rounds === 0
  const noHolders = data && data.sample.team_rounds > 0 && data.sample.holder_team_rounds === 0

  return (
    <main className="workspace">
      {/* ---------------------------------------------------------------- left: rates */}
      <aside className="panel left" aria-label={`${wname} rates`}>
        <Section title={`${wname} economy`} hint="At buy end, per team-round in scope">
          {o ? (
            <div className="tiles">
              <StatTile label="Possession" frac={o.possession} />
              <StatTile label="Bought" frac={o.bought} />
              <StatTile label={`Win with ${wname}`} frac={o.round_win_with} />
              <StatTile label="Win without" frac={o.round_win_without} />
            </div>
          ) : <Empty>Loading…</Empty>}
        </Section>

        <Section title="How it was acquired" hint="Per holder-round: bought themselves, dropped by a teammate, or carried over alive from the previous round">
          {data && acqTotal > 0 ? (
            <>
              <SegBar parts={acqParts} height={12} />
              <Legend items={acqParts.map((p) => ({
                key: p.key, color: p.color,
                label: <>{p.label} <span className="num">{p.value} · {Math.round((100 * p.value) / acqTotal)}%</span></>,
              }))} />
            </>
          ) : <Empty>No holder rounds.</Empty>}
        </Section>

        <Section title="By side">
          {data ? (
            <table className="tbl">
              <thead><tr><th /><th>Possession</th><th>Bought</th><th>Win w/</th><th>Win w/o</th></tr></thead>
              <tbody>
                {(['defender', 'attacker'] as Side[]).map((sd) => {
                  const r = data.rates.by_side[sd]
                  if (!r?.team_rounds) return null
                  return (
                    <tr key={sd}>
                      <th>{SIDE_LABEL[sd]}</th>
                      <td title={r.possession.fraction}>{pct(r.possession)}<small>{r.possession.fraction}</small></td>
                      <td title={r.bought.fraction}>{pct(r.bought)}<small>{r.bought.fraction}</small></td>
                      <td>{pct(r.round_win_with)}<small>{r.round_win_with.fraction}</small></td>
                      <td>{pct(r.round_win_without)}<small>{r.round_win_without.fraction}</small></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : null}
        </Section>

        <Section title="Possession by buy tier">
          {data && Object.entries(data.rates.by_buy_tier).map(([tier, r]) => (
            <FracBar key={tier} label={tier} frac={r!.possession}
              title={`Bought in ${fracText(r!.bought)}; win with ${fracText(r!.round_win_with)}`} />
          ))}
        </Section>

        <Section title="By agent">
          {data?.agents.length ? (
            <ul className="agent-list">
              {data.agents.map((a) => {
                const ag = lookups.agent.get(a.agent)
                return (
                  <li key={a.agent}>
                    {ag?.display_icon && <img src={ag.display_icon} alt="" />}
                    <span>{a.name}</span>
                    <span className="num">{fracText(a.possession)}</span>
                  </li>
                )
              })}
            </ul>
          ) : <Empty>No holders.</Empty>}
        </Section>
      </aside>

      {/* ---------------------------------------------------------------- center: minimap */}
      <section className="panel center" aria-label={`${wname} map`}>
        <MapHeader map={map} sel={sel} lookups={lookups} lensToggle={lensToggle} loading={loading} />
        <div className="map-toolbar">
          <PhasePills active={phases} onChange={setPhases} counts={posCounts} countLabel="positions" />
          <div className="layer-checks">
            {sel.side ? (
              <span className="chip static">{SIDE_LABEL[sel.side]} only</span>
            ) : (
              <Toggle size="sm" ariaLabel="Setup side" value={sideView} onChange={setSideView} options={[
                { value: 'defender', label: 'Defense' }, { value: 'attacker', label: 'Attack' }, { value: 'both', label: 'Both' },
              ]} />
            )}
          </div>
        </div>
        <div className="map-toolbar sub">
          <div className="layer-checks">
            <Check checked={groups.kills} onChange={(v) => setGroups({ ...groups, kills: v })}>
              <ShapeGlyph shape="dot" color="#c3c2b7" /> Kills <ShapeGlyph shape="ring" color="#c3c2b7" /> other weapon
            </Check>
            <Check checked={groups.deaths} onChange={(v) => setGroups({ ...groups, deaths: v })}>
              <ShapeGlyph shape="cross" color="#c3c2b7" /> Deaths
            </Check>
            <Check checked={groups.utility} onChange={(v) => setGroups({ ...groups, utility: v })}>
              <ShapeGlyph shape="small" color="#c3c2b7" /> Utility
            </Check>
            <Check checked={showSightlines} onChange={setShowSightlines}>
              <ShapeGlyph shape="arrow" color={SIGHTLINE} /> Kill sightlines
            </Check>
            <Check checked={showDuels} onChange={setShowDuels}>
              <span className="duel-glyph good" /> <span className="duel-glyph bad" /> Opening duels
            </Check>
          </div>
        </div>
        <div className="map-stage">
          <Minimap map={map} points={points} vectors={vectors} bubbles={bubbles} loading={loading && !data}
            empty={error ? <ErrorNote error={error} />
              : noRounds ? 'No rounds match these filters.'
                : noHolders ? `No ${wname} holders in ${data!.sample.team_rounds} team-rounds.` : null} />
        </div>
        <p className="map-foot muted">
          Positions of the {wname} holder, colored by round timing. Arrows: {wname} kill sightlines (killer → victim,
          thicker = more kills; both sides). Circles: opening-duel record at that callout (green = won half or more).
        </p>
      </section>

      {/* ---------------------------------------------------------------- right: profile */}
      <aside className="panel right" aria-label={`${wname} profile`}>
        <Section title={self ? `Your ${wname} habits` : `${wname} threat profile`}>
          {data?.summaries.length ? (
            <ul className="cards">{data.summaries.map((t, i) => <li key={i} className="card insight">{t}</li>)}</ul>
          ) : <Empty>{loading ? 'Loading…' : 'No data.'}</Empty>}
        </Section>

        <Section title="Holders" hint="Possession = player-rounds holding the weapon at buy end">
          {data?.players.length ? (
            <ul className="holders">
              {data.players.map((p) => (
                <li key={p.player_id}>
                  <div className="holder-head">
                    <strong>{p.name}</strong>
                    <span className="num">{fracText(p.possession)}</span>
                  </div>
                  <SegBar height={8} parts={acqParts.map((a) => ({ ...a, value: p.acquired[a.key] }))} />
                  <div className="holder-meta muted">
                    {p.acquired.bought_self} bought · {p.acquired.received_drop} dropped · {p.acquired.carried_over} carried
                    {p.bought_for_teammates ? ` · bought ${p.bought_for_teammates} for teammates` : ''}
                    {Object.entries(p.by_side).map(([sd, f]) => ` · ${SIDE_LABEL[sd as Side]} ${pct(f)}`).join('')}
                  </div>
                </li>
              ))}
            </ul>
          ) : <Empty>No holders.</Empty>}
        </Section>

        <Section title="Hand-offs" hint="Drops: teammate buys it for a holder. Round-to-round: the weapon changes hands between consecutive rounds">
          {data && (data.possession_shifts.drops.length || data.possession_shifts.round_to_round.length) ? (
            <ul className="handoffs">
              {data.possession_shifts.drops.map((d) => (
                <li key={`d-${d.buyer_id}-${d.recipient_id}`}>
                  <span className="tag">Drop</span> {d.buyer} <span className="arrow">→</span> {d.recipient}
                  <span className="num">×{d.times}</span>
                </li>
              ))}
              {data.possession_shifts.round_to_round.map((d) => (
                <li key={`r-${d.from_id}-${d.to_id}`}>
                  <span className="tag">Next round</span> {d.from_player} <span className="arrow">→</span> {d.to_player}
                  <span className="num">×{d.times}</span>
                </li>
              ))}
            </ul>
          ) : <Empty>No hand-offs between players.</Empty>}
        </Section>

        <Section title="Setup positions by timing" hint="Holder rounds where the holder was seen (kill, death or utility) at a callout in each window">
          {data && setupSides.map((sd) => {
            const st = data.setups[sd]
            if (!st) return <Empty key={sd}>No {SIDE_LABEL[sd].toLowerCase()} holder rounds.</Empty>
            return (
              <div key={sd} className="setup">
                <h4>{SIDE_LABEL[sd]} <span className="muted">· {st.holder_rounds} holder rounds</span></h4>
                {st.phases.map((ph) => (
                  <div key={ph.phase} className="setup-phase">
                    <div className="setup-phase-head">
                      <ShapeGlyph shape={ph.phase === 'buy_phase' ? 'diamond' : 'dot'} color={PHASE_META[ph.phase].color} />
                      {PHASE_META[ph.phase].label}
                      <span className="num muted">seen in {ph.rounds_with_position.fraction}</span>
                    </div>
                    {ph.top_callouts.length ? (
                      <ul className="setup-callouts">
                        {ph.top_callouts.slice(0, 4).map((c) => (
                          <li key={c.callout}><span>{c.callout}</span><span className="num">{fracText(c.rounds)}</span></li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ))}
              </div>
            )
          })}
        </Section>

        <Section title="Opening duels" hint="First kill of the round involving a holder">
          {data && data.opening_duels.total.win_rate.of > 0 ? (
            <>
              <p className="duel-total">
                <strong>{data.opening_duels.total.won}W – {data.opening_duels.total.lost}L</strong>
                <span className="muted"> · {fracText(data.opening_duels.total.win_rate)} won</span>
              </p>
              <table className="tbl">
                <thead><tr><th>Callout</th><th>Side</th><th>W–L</th><th>Win</th></tr></thead>
                <tbody>
                  {data.opening_duels.by_callout.map((d) => (
                    <tr key={`${d.callout}-${d.side}`} className={sideOk(d.side) ? '' : 'faded'}>
                      <th>{d.callout ?? '–'}</th>
                      <td>{SIDE_LABEL[d.side]}</td>
                      <td>
                        <span className={`wl ${(d.win_rate.pct ?? 0) >= 50 ? 'good' : 'bad'}`}>
                          {(d.win_rate.pct ?? 0) >= 50 ? '▲' : '▼'}
                        </span> {d.won}–{d.lost}
                      </td>
                      <td>{pct(d.win_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : <Empty>No opening duels taken by holders.</Empty>}
        </Section>

        <Section title="Kill sightlines" hint={`${wname} kills grouped by killer and victim callout`}>
          {data?.angles.length ? (
            <ul className="handoffs">
              {data.angles.map((a) => (
                <li key={`${a.from_callout}-${a.to_callout}`}>
                  {a.from_callout ?? '?'} <span className="arrow">→</span> {a.to_callout ?? '?'}
                  <span className="num">{a.kills} kill{a.kills === 1 ? '' : 's'} · ~{a.median_time_s ?? '–'}s</span>
                </li>
              ))}
            </ul>
          ) : <Empty>No {wname} kills.</Empty>}
          {data && Object.values(data.weapon_kills_by_phase).some(Boolean) && (
            <div className="kills-phase">
              {IN_ROUND.map((ph) => {
                const total = IN_ROUND.reduce((s, x) => s + data.weapon_kills_by_phase[x], 0)
                const n = data.weapon_kills_by_phase[ph]
                return (
                  <FracBar key={ph} swatch color={PHASE_META[ph].color} label={`Kills ${PHASE_META[ph].label}`}
                    frac={{ n, of: total, fraction: `${n}/${total}`, pct: total ? (100 * n) / total : null }} />
                )
              })}
            </div>
          )}
        </Section>
      </aside>
    </main>
  )
}

function PositionTip({ p, lookups, wname }: { p: WeaponPosition; lookups: Lookups; wname: string }) {
  const k = kindInfo(p.kind)
  return (
    <>
      <div className="tip-title">{k.label === 'Kill with weapon' ? `Kill with ${wname}` : k.label}</div>
      <dl>
        <dt>Player</dt><dd>{lookups.player.get(p.player_id)?.name ?? p.player_id}</dd>
        <dt>Callout</dt><dd>{p.callout ?? '–'}</dd>
        <dt>Timing</dt><dd>{p.phase ? PHASE_META[p.phase].label : '–'}{p.time_s !== null && p.time_s >= 0 ? ` (${fmtTime(p.time_s)})` : ''}</dd>
        <dt>Round</dt><dd>R{p.round_number} · {SIDE_LABEL[p.side]}</dd>
      </dl>
    </>
  )
}
