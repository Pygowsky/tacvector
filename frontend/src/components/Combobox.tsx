import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

export interface Option {
  value: string
  label: string
  sublabel?: string
  meta?: string
  icon?: string | null
  /** Rendered but not selectable-looking; e.g. maps with no data for the team. */
  dim?: boolean
}

interface Props {
  label: string
  value: string | null
  options: Option[]
  onChange: (value: string | null) => void
  placeholder?: string
  /** Label of the "clear" choice; omit to make the field required. */
  clearLabel?: string
  /** Extra content above the option list (e.g. preset chips). Receives a `close` callback. */
  header?: (close: () => void) => ReactNode
  disabled?: boolean
  disabledReason?: string
  width?: number
  searchable?: boolean
}

/** Accessible searchable dropdown: type to filter, arrows to move, Enter to pick, Esc to close. */
export function Combobox({
  label, value, options, onChange, placeholder = 'Any', clearLabel, header, disabled, disabledReason,
  width = 190, searchable,
}: Props) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const id = useId()
  const showSearch = searchable ?? options.length > 7

  const selected = options.find((o) => o.value === value) ?? null

  const items = useMemo(() => {
    const q = query.trim().toLowerCase()
    const matched = q
      ? options.filter((o) => `${o.label} ${o.sublabel ?? ''}`.toLowerCase().includes(q))
      : options
    const all: (Option | null)[] = clearLabel && !q ? [null, ...matched] : matched
    return all
  }, [options, query, clearLabel])

  const close = () => {
    setOpen(false)
    setQuery('')
  }

  const openList = () => {
    // Start keyboard navigation at the current selection (items is unfiltered here: query is '').
    setActive(Math.max(0, items.findIndex((o) => (o?.value ?? null) === value)))
    setOpen(true)
  }

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) close()
    }
    document.addEventListener('mousedown', onDown)
    requestAnimationFrame(() => input.current?.focus())
    return () => document.removeEventListener('mousedown', onDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [active, open])

  const pick = (o: Option | null) => {
    onChange(o ? o.value : null)
    close()
  }

  const onKey = (e: KeyboardEvent) => {
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        openList()
      }
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      close()
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(items.length - 1, a + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(0, a - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (items[active] !== undefined) pick(items[active])
    }
  }

  return (
    <div className="cb" ref={root} style={{ width }} onKeyDown={onKey}>
      <span className="cb-label" id={`${id}-label`}>{label}</span>
      <button
        type="button"
        className={`cb-button${value ? ' has-value' : ''}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-labelledby={`${id}-label`}
        disabled={disabled}
        title={disabled ? disabledReason : selected?.label}
        onClick={() => (open ? close() : openList())}
      >
        {selected?.icon && <img className="cb-icon" src={selected.icon} alt="" />}
        <span className="cb-value">{selected ? selected.label : clearLabel ?? placeholder}</span>
        <svg className="cb-caret" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
          <path d="M1.5 3.5 5 7l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      </button>
      {open && (
        <div className="cb-pop" role="dialog" aria-label={label}>
          {header?.(close)}
          {showSearch && (
            <input
              ref={input}
              className="cb-search"
              placeholder={`Search ${options.length} ${label.toLowerCase()}…`}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setActive(0)
              }}
              aria-controls={`${id}-list`}
              aria-activedescendant={`${id}-opt-${active}`}
            />
          )}
          <ul className="cb-list" role="listbox" id={`${id}-list`} ref={list} tabIndex={-1}>
            {items.length === 0 && <li className="cb-empty">No matches</li>}
            {items.map((o, i) => (
              <li
                key={o?.value ?? '__clear'}
                id={`${id}-opt-${i}`}
                data-idx={i}
                role="option"
                aria-selected={(o?.value ?? null) === value}
                className={[
                  'cb-opt',
                  i === active && 'active',
                  (o?.value ?? null) === value && 'selected',
                  o?.dim && 'dim',
                  !o && 'clear',
                ].filter(Boolean).join(' ')}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(o)}
              >
                {o?.icon && <img className="cb-icon" src={o.icon} alt="" loading="lazy" />}
                <span className="cb-opt-text">
                  <span>{o ? o.label : clearLabel}</span>
                  {o?.sublabel && <small>{o.sublabel}</small>}
                </span>
                {o?.meta && <span className="cb-meta">{o.meta}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
