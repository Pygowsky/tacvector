import type { ReactNode } from 'react'
import { fracText, pct } from '../theme'
import type { Frac } from '../types'

export function Section({ title, hint, children, right }: { title: string; hint?: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="section">
      <header className="section-head">
        <h3 title={hint}>{title}</h3>
        {right}
      </header>
      {children}
    </section>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>
}

/** Big number with its n/N underneath: the number is the chart. */
export function StatTile({ label, frac, sub, tone }: { label: string; frac: Frac; sub?: string; tone?: 'good' | 'bad' }) {
  return (
    <div className={`stat-tile${tone ? ` ${tone}` : ''}`}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{pct(frac)}</span>
      <span className="stat-sub">{sub ?? frac.fraction}</span>
    </div>
  )
}

/** Thin horizontal bar: label left, "pct · n/N" right, 6px track below. */
export function FracBar({ label, frac, color = 'var(--series-1)', swatch, title }: {
  label: ReactNode; frac: Frac; color?: string; swatch?: boolean; title?: string
}) {
  const w = frac.of ? Math.max(0, Math.min(100, (100 * frac.n) / frac.of)) : 0
  return (
    <div className="frac-bar" title={title}>
      <div className="frac-bar-row">
        <span className="frac-bar-label">
          {swatch && <i className="swatch" style={{ background: color }} />}
          {label}
        </span>
        <span className="frac-bar-value">{fracText(frac)}</span>
      </div>
      <div className="track" role="presentation">
        <div className="fill" style={{ width: `${w}%`, background: color }} />
      </div>
    </div>
  )
}

/** Segmented part-to-whole bar with a 2px surface gap between segments. */
export function SegBar({ parts, height = 10 }: { parts: { key: string; label: string; value: number; color: string }[]; height?: number }) {
  const total = parts.reduce((s, p) => s + p.value, 0)
  return (
    <div className="seg-bar" style={{ height }} role="img"
      aria-label={parts.map((p) => `${p.label} ${p.value}`).join(', ')}>
      {total === 0 && <div className="seg empty-seg" />}
      {parts.filter((p) => p.value > 0).map((p) => (
        <div key={p.key} className="seg" title={`${p.label}: ${p.value} (${Math.round((100 * p.value) / total)}%)`}
          style={{ flexGrow: p.value, background: p.color }} />
      ))}
    </div>
  )
}

export function Legend({ items }: { items: { key: string; label: ReactNode; color?: string; shape?: ReactNode }[] }) {
  return (
    <ul className="legend">
      {items.map((it) => (
        <li key={it.key}>
          {it.shape ?? <i className="swatch" style={{ background: it.color }} />}
          {it.label}
        </li>
      ))}
    </ul>
  )
}

export function ConfidenceBadge({ level }: { level: 'high' | 'medium' | 'low' }) {
  const icon = level === 'high' ? '●●●' : level === 'medium' ? '●●○' : '●○○'
  return <span className={`badge conf-${level}`} title={`${level} confidence (sample size & share)`}>{icon} {level}</span>
}

export function Toggle<T extends string>({ value, options, onChange, size = 'md', ariaLabel }: {
  value: T; options: { value: T; label: ReactNode; title?: string }[]; onChange: (v: T) => void; size?: 'sm' | 'md'; ariaLabel: string
}) {
  return (
    <div className={`toggle ${size}`} role="radiogroup" aria-label={ariaLabel}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={o.value === value} title={o.title}
          className={o.value === value ? 'on' : ''} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Check({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode }) {
  return (
    <label className="check">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {children}
    </label>
  )
}

/** Shape glyphs matching the canvas marks, for legends. */
export function ShapeGlyph({ shape, color }: { shape: 'dot' | 'diamond' | 'ring' | 'cross' | 'small' | 'arrow'; color: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" className="glyph">
      {shape === 'dot' && <circle cx="7" cy="7" r="4" fill={color} />}
      {shape === 'small' && <circle cx="7" cy="7" r="2.75" fill={color} />}
      {shape === 'ring' && <circle cx="7" cy="7" r="4.25" fill="none" stroke={color} strokeWidth="1.75" />}
      {shape === 'diamond' && <path d="M7 2 12 7 7 12 2 7Z" fill="none" stroke={color} strokeWidth="1.75" />}
      {shape === 'cross' && <path d="M3 3l8 8M11 3l-8 8" stroke={color} strokeWidth="2" strokeLinecap="round" />}
      {shape === 'arrow' && <path d="M1 7h9M7 3.5 11 7 7 10.5" fill="none" stroke={color} strokeWidth="1.75" strokeLinecap="round" />}
    </svg>
  )
}
