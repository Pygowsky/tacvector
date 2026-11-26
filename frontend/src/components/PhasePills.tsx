import { PHASE_META, PHASES } from '../theme'
import type { Phase } from '../types'
import { ShapeGlyph } from './ui'

interface Props {
  active: Set<Phase>
  onChange: (next: Set<Phase>) => void
  counts?: Partial<Record<Phase, number>>
  /** Phases that cannot occur in this lens (e.g. no kills during the buy phase). */
  phases?: Phase[]
  countLabel?: string
}

/** Round-timing filter: each pill is also the color/shape legend for the map. */
export function PhasePills({ active, onChange, counts, phases = PHASES, countLabel = 'casts' }: Props) {
  const all = phases.every((p) => active.has(p))
  const toggle = (p: Phase) => {
    const next = new Set(active)
    if (all) {
      // From "everything", a click isolates that window, which is the common intent.
      next.clear()
      next.add(p)
    } else if (next.has(p)) {
      next.delete(p)
      if (next.size === 0) phases.forEach((x) => next.add(x))
    } else {
      next.add(p)
    }
    onChange(next)
  }
  return (
    <div className="phase-pills" role="group" aria-label="Round timing filter">
      <span className="row-label">Timing</span>
      <button type="button" className={`pill${all ? ' on' : ''}`} onClick={() => onChange(new Set(phases))}>All</button>
      {phases.map((p) => {
        const m = PHASE_META[p]
        const on = active.has(p) && !all
        return (
          <button key={p} type="button" className={`pill${on ? ' on' : ''}${!active.has(p) ? ' off' : ''}`}
            aria-pressed={active.has(p)} title={m.hint} onClick={() => toggle(p)}>
            <ShapeGlyph shape={p === 'buy_phase' ? 'diamond' : 'dot'} color={m.color} />
            {m.label}
            {counts?.[p] !== undefined && <span className="pill-count" title={countLabel}>{counts[p]!.toLocaleString()}</span>}
          </button>
        )
      })}
    </div>
  )
}
