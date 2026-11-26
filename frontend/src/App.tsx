import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiGet, auth, AuthError, setAuthHandler, useApi } from './api'
import { ControlBar } from './components/ControlBar'
import { LoginModal } from './components/LoginModal'
import { Toggle } from './components/ui'
import { buildLookups } from './lookups'
import { EMPTY, readUrl, writeUrl, type Lens, type Selection } from './selection'
import type { FiltersResponse, OperatorResponse } from './types'
import { OperatorView } from './views/OperatorView'
import { ErrorNote } from './views/shared'
import { UtilityView } from './views/UtilityView'

/** Validate URL-provided ids against /api/filters and fill in defaults. */
function initialSelection(f: FiltersResponse): { sel: Selection; mapPinned: boolean } {
  const { sel: fromUrl, hasTeamParam } = readUrl()
  const teamIds = new Set(f.teams.map((t) => t.team_id))
  const sel: Selection = { ...EMPTY, ...fromUrl }
  if (sel.team && !teamIds.has(sel.team)) sel.team = null
  if (!hasTeamParam || (fromUrl.team && !sel.team)) {
    // Default: the first Self-Scout preset (if the dataset has one), otherwise the busiest team.
    const preset = f.self_scout_presets[0]
    sel.team = preset?.team ?? f.teams[0]?.team_id ?? null
    if (preset && !fromUrl.mode) sel.mode = 'self'
  }
  if (sel.map && !f.maps.some((m) => m.slug === sel.map)) sel.map = null
  if (sel.player && !f.players.some((p) => p.player_id === sel.player)) sel.player = null
  if (sel.agent && !f.agents.some((a) => a.slug === sel.agent)) sel.agent = null
  if (sel.side && sel.side !== 'attacker' && sel.side !== 'defender') sel.side = null
  if (sel.buyTier && !f.buy_tiers.some((t) => t.id === sel.buyTier)) sel.buyTier = null
  if (sel.category && !f.ability_categories.some((c) => c.category === sel.category)) sel.category = null
  return { sel, mapPinned: !!sel.map }
}

export default function App() {
  const [nonce, setNonce] = useState(0)
  const [login, setLogin] = useState<{ rejected: boolean } | null>(null)

  useEffect(() => {
    setAuthHandler(() => setLogin({ rejected: !!auth.get() }))
    return () => setAuthHandler(null)
  }, [])

  const filtersQ = useApi<FiltersResponse>('filters', {}, nonce)
  const filters = filtersQ.data
  const lookups = useMemo(() => (filters ? buildLookups(filters) : null), [filters])

  const [sel, setSel] = useState<Selection | null>(null)
  const mapPinned = useRef(false)

  useEffect(() => {
    if (!filters || sel) return
    const init = initialSelection(filters)
    mapPinned.current = init.mapPinned
    setSel(init.sel)
  }, [filters, sel])

  useEffect(() => {
    if (sel) writeUrl(sel)
  }, [sel])

  const update = useCallback((patch: Partial<Selection>, opts?: { pinMap?: boolean }) => {
    setSel((s) => {
      if (!s) return s
      if (patch.team !== undefined && patch.team !== s.team) mapPinned.current = false
      if (opts?.pinMap) mapPinned.current = true
      return { ...s, ...patch }
    })
  }, [])

  // ---- default map: the one with the most team-rounds for the selected team --------------- //
  const [teamMapRounds, setTeamMapRounds] = useState<{ team: string; rounds: Record<string, number> } | null>(null)
  const team = sel?.team
  const hasSel = !!sel
  useEffect(() => {
    if (!filters || !hasSel) return
    if (!team) {
      if (!mapPinned.current) {
        const busiest = [...filters.maps].sort((a, b) => b.games - a.games)[0]
        update({ map: busiest?.slug ?? null })
      }
      return
    }
    const ac = new AbortController()
    apiGet<OperatorResponse>('operator', { team }, ac.signal)
      .then((d) => {
        const rounds = Object.fromEntries(Object.entries(d.rates.by_map).map(([m, r]) => [m, r.team_rounds]))
        setTeamMapRounds({ team, rounds })
        if (!mapPinned.current) {
          const best = Object.entries(rounds).sort((a, b) => b[1] - a[1])[0]?.[0]
          if (best) update({ map: best })
          else update({ map: [...filters.maps].sort((a, b) => b.games - a.games)[0]?.slug ?? null })
        }
      })
      .catch((e: Error) => {
        if (ac.signal.aborted || e instanceof AuthError) return
        if (!mapPinned.current) update({ map: filters.maps[0]?.slug ?? null })
      })
    return () => ac.abort()
  }, [team, filters, hasSel, nonce, update])

  const onLoggedIn = () => {
    setLogin(null)
    setNonce((n) => n + 1)
  }

  // ---- render ------------------------------------------------------------------------------ //
  if (login) return <LoginModal rejected={login.rejected} onSuccess={onLoggedIn} />

  if (!filters || !lookups || !sel) {
    return (
      <div className="boot">
        <div className="brand"><span className="brand-mark" aria-hidden="true">TV</span> TacVector</div>
        {filtersQ.error && !(filtersQ.error instanceof AuthError) ? (
          <div className="boot-error">
            <ErrorNote error={filtersQ.error} />
            <button className="btn" type="button" onClick={() => setNonce((n) => n + 1)}>Retry</button>
          </div>
        ) : (
          <p className="muted"><span className="spinner sm" /> Loading teams, maps and players…</p>
        )}
      </div>
    )
  }

  const lensToggle = (
    <Toggle<Lens>
      ariaLabel="Map lens"
      value={sel.lens}
      onChange={(lens) => update({ lens })}
      options={[
        { value: 'utility', label: 'Utility Placement', title: 'Where and when utility lands' },
        { value: 'operator', label: 'Operator & Odin', title: 'High-impact weapon possession, setups and duels' },
      ]}
    />
  )
  const teamRounds = teamMapRounds && teamMapRounds.team === sel.team ? teamMapRounds.rounds : undefined

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">TV</span>
          <div>
            <strong>TacVector</strong>
            <small>VALORANT scouting · {filters.teams.length} teams</small>
          </div>
        </div>
        <ControlBar filters={filters} sel={sel} update={update} teamMapRounds={teamRounds} />
      </header>
      {!sel.map ? (
        <main className="workspace"><p className="muted center-note"><span className="spinner sm" /> Finding the map with the most data…</p></main>
      ) : sel.lens === 'utility' ? (
        <UtilityView sel={sel} lookups={lookups} nonce={nonce} lensToggle={lensToggle} />
      ) : (
        <OperatorView sel={sel} lookups={lookups} nonce={nonce} lensToggle={lensToggle} />
      )}
    </div>
  )
}
