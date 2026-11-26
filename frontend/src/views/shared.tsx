import type { ReactNode } from 'react'
import { ApiError, AuthError } from '../api'
import type { Lookups } from '../lookups'
import type { Selection } from '../selection'
import { SIDE_LABEL } from '../theme'
import type { MapInfo } from '../types'

export function MapHeader({ map, sel, lookups, lensToggle, loading }: {
  map: MapInfo; sel: Selection; lookups: Lookups; lensToggle: ReactNode; loading: boolean
}) {
  const team = sel.team ? lookups.team.get(sel.team)?.name ?? sel.team : 'All teams'
  const scope = [
    sel.side && SIDE_LABEL[sel.side],
    sel.buyTier,
    sel.player && (lookups.player.get(sel.player)?.name ?? sel.player),
    sel.agent && (lookups.agent.get(sel.agent)?.name ?? sel.agent),
  ].filter(Boolean)
  return (
    <header className="map-header">
      <div className="map-title">
        <span className={`mode-badge ${sel.mode}`}>{sel.mode === 'self' ? 'Self-Scout' : 'Opponent Scout'}</span>
        <h2>{team} <span className="muted">on</span> {map.name}</h2>
        {scope.length > 0 && <span className="scope-chips">{scope.map((s) => <span key={s as string} className="chip static">{s}</span>)}</span>}
        {loading && <span className="spinner sm" aria-label="Loading" />}
      </div>
      {lensToggle}
    </header>
  )
}

export function ErrorNote({ error }: { error: Error }) {
  if (error instanceof AuthError) return <span>Password required.</span>
  if (error instanceof ApiError) {
    return (
      <span>
        <strong>{error.status === 422 ? 'Filters need narrowing' : `Error ${error.status}`}</strong>
        <br />
        {error.message}
      </span>
    )
  }
  return (
    <span>
      <strong>Can't reach the API</strong>
      <br />
      Is the backend running on 127.0.0.1:8000? ({error.message})
    </span>
  )
}
