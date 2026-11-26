import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import type { MapInfo } from '../types'

export type PointShape = 'dot' | 'diamond' | 'ring' | 'cross' | 'small'

export interface MapPoint {
  u: number
  v: number
  color: string
  shape: PointShape
  /** Unit aim direction in minimap space; drawn as a short tick. */
  aim?: [number, number] | null
  tip: ReactNode
}

export interface MapVector {
  from: [number, number]
  to: [number, number]
  color: string
  width: number
  tip: ReactNode
}

export interface MapBubble {
  key: string
  u: number
  v: number
  /** 0..1 relative weight; sets the radius. */
  weight: number
  title: string
  sub: string
  tone?: 'neutral' | 'good' | 'bad'
  showLabel: boolean
  tip: ReactNode
}

interface Props {
  map: MapInfo
  points: MapPoint[]
  vectors?: MapVector[]
  bubbles?: MapBubble[]
  showCalloutNames?: boolean
  loading?: boolean
  empty?: ReactNode
  children?: ReactNode
}

const VB = 1000 // SVG viewBox size; u/v are scaled into it
const RING = 'rgba(10, 11, 14, 0.92)'
const TONE = {
  neutral: { fill: 'rgba(240,239,236,0.10)', stroke: 'rgba(240,239,236,0.55)' },
  good: { fill: 'rgba(12,163,12,0.18)', stroke: '#0ca30c' },
  bad: { fill: 'rgba(208,59,59,0.18)', stroke: '#d03b3b' },
}

/** `src` is the array the index refers to, so a hover left over from older data is ignored. */
type Hover = { kind: 'point' | 'vector' | 'bubble'; i: number; x: number; y: number; src: unknown[] } | null

function drawShape(ctx: CanvasRenderingContext2D, p: MapPoint, x: number, y: number) {
  ctx.lineJoin = 'round'
  switch (p.shape) {
    case 'diamond': {
      const r = 5
      ctx.beginPath()
      ctx.moveTo(x, y - r)
      ctx.lineTo(x + r, y)
      ctx.lineTo(x, y + r)
      ctx.lineTo(x - r, y)
      ctx.closePath()
      ctx.lineWidth = 3.5
      ctx.strokeStyle = RING
      ctx.stroke()
      ctx.lineWidth = 1.75
      ctx.strokeStyle = p.color
      ctx.stroke()
      break
    }
    case 'ring': {
      ctx.beginPath()
      ctx.arc(x, y, 4.25, 0, Math.PI * 2)
      ctx.lineWidth = 3.5
      ctx.strokeStyle = RING
      ctx.stroke()
      ctx.lineWidth = 1.75
      ctx.strokeStyle = p.color
      ctx.stroke()
      break
    }
    case 'cross': {
      const r = 4
      ctx.beginPath()
      ctx.moveTo(x - r, y - r)
      ctx.lineTo(x + r, y + r)
      ctx.moveTo(x + r, y - r)
      ctx.lineTo(x - r, y + r)
      ctx.lineCap = 'round'
      ctx.lineWidth = 4.5
      ctx.strokeStyle = RING
      ctx.stroke()
      ctx.lineWidth = 2
      ctx.strokeStyle = p.color
      ctx.stroke()
      break
    }
    default: {
      const r = p.shape === 'small' ? 2.75 : 4
      ctx.beginPath()
      ctx.arc(x, y, r, 0, Math.PI * 2)
      ctx.fillStyle = p.color
      ctx.fill()
      ctx.lineWidth = 1.5
      ctx.strokeStyle = RING
      ctx.stroke()
    }
  }
}

function segDist(px: number, py: number, ax: number, ay: number, bx: number, by: number) {
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  const t = len2 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy))
}

export function Minimap({ map, points, vectors = [], bubbles = [], showCalloutNames, loading, empty, children }: Props) {
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState(0)
  const [hover, setHover] = useState<Hover>(null)
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const imgFailed = failedUrl !== null && failedUrl === map.minimap_url

  // The minimap art has generous padding: zoom to the callout bounds so the playable area fills
  // the frame. Every layer lives on one "stage" of side `full`, translated by (ox, oy).
  const view = useMemo(() => fitView(map), [map])
  const full = size / view.span
  const ox = view.x0 * full
  const oy = view.y0 * full

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => setSize(Math.floor(el.clientWidth)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ---- canvas: vectors under points, points with aim ticks ------------------------------- //
  useEffect(() => {
    const c = canvas.current
    if (!c || !size) return
    const dpr = window.devicePixelRatio || 1
    c.width = Math.round(full * dpr)
    c.height = Math.round(full * dpr)
    const ctx = c.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, full, full)

    for (const vec of vectors) {
      const [ax, ay] = [vec.from[0] * full, vec.from[1] * full]
      const [bx, by] = [vec.to[0] * full, vec.to[1] * full]
      const ang = Math.atan2(by - ay, bx - ax)
      ctx.lineCap = 'round'
      ctx.strokeStyle = RING
      ctx.lineWidth = vec.width + 2.5
      ctx.beginPath()
      ctx.moveTo(ax, ay)
      ctx.lineTo(bx, by)
      ctx.stroke()
      ctx.strokeStyle = vec.color
      ctx.lineWidth = vec.width
      ctx.stroke()
      const head = 7 + vec.width
      ctx.beginPath()
      ctx.moveTo(bx, by)
      ctx.lineTo(bx - head * Math.cos(ang - 0.45), by - head * Math.sin(ang - 0.45))
      ctx.lineTo(bx - head * Math.cos(ang + 0.45), by - head * Math.sin(ang + 0.45))
      ctx.closePath()
      ctx.fillStyle = vec.color
      ctx.fill()
      ctx.beginPath()
      ctx.arc(ax, ay, 3 + vec.width / 2, 0, Math.PI * 2)
      ctx.fill()
    }

    const tick = Math.max(9, size / 70)
    ctx.lineCap = 'round'
    ctx.globalAlpha = 0.75
    ctx.lineWidth = 1.5
    for (const p of points) {
      if (!p.aim) continue
      const x = p.u * full
      const y = p.v * full
      ctx.strokeStyle = p.color
      ctx.beginPath()
      ctx.moveTo(x, y)
      ctx.lineTo(x + p.aim[0] * tick, y + p.aim[1] * tick)
      ctx.stroke()
    }
    ctx.globalAlpha = 1
    for (const p of points) drawShape(ctx, p, p.u * full, p.v * full)
  }, [points, vectors, size, full])

  // ---- hit testing ------------------------------------------------------------------------ //
  const grid = useMemo(() => {
    // Bucket points into a coarse grid so hover stays cheap with thousands of casts.
    const cells = new Map<number, number[]>()
    points.forEach((p, i) => {
      const k = Math.floor(p.u * 50) * 100 + Math.floor(p.v * 50)
      const arr = cells.get(k)
      if (arr) arr.push(i)
      else cells.set(k, [i])
    })
    return cells
  }, [points])

  const onMove = useCallback(
    (e: MouseEvent<HTMLDivElement>) => {
      if (!size) return
      const rect = e.currentTarget.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top
      const sx = x + ox // stage coordinates
      const sy = y + oy
      const u = sx / full
      const v = sy / full
      let best = -1
      let bestD = 9
      const cu = Math.floor(u * 50)
      const cv = Math.floor(v * 50)
      for (let du = -1; du <= 1; du++) {
        for (let dv = -1; dv <= 1; dv++) {
          for (const i of grid.get((cu + du) * 100 + cv + dv) ?? []) {
            const d = Math.hypot(points[i].u * full - sx, points[i].v * full - sy)
            if (d < bestD) {
              bestD = d
              best = i
            }
          }
        }
      }
      if (best >= 0) return setHover({ kind: 'point', i: best, x, y, src: points })
      for (let i = 0; i < vectors.length; i++) {
        const vec = vectors[i]
        const d = segDist(sx, sy, vec.from[0] * full, vec.from[1] * full, vec.to[0] * full, vec.to[1] * full)
        if (d < 6) return setHover({ kind: 'vector', i, x, y, src: vectors })
      }
      for (let i = 0; i < bubbles.length; i++) {
        const b = bubbles[i]
        const r = bubbleRadius(b.weight) * view.span * (full / VB)
        if (Math.hypot(b.u * full - sx, b.v * full - sy) < r) return setHover({ kind: 'bubble', i, x, y, src: bubbles })
      }
      setHover(null)
    },
    [size, full, ox, oy, view.span, grid, points, vectors, bubbles],
  )

  const current = hover?.kind === 'point' ? points : hover?.kind === 'vector' ? vectors : bubbles
  const live = hover && hover.src === current ? hover : null
  const tip =
    live?.kind === 'point' ? points[live.i]?.tip
      : live?.kind === 'vector' ? vectors[live.i]?.tip
        : live?.kind === 'bubble' ? bubbles[live.i]?.tip
          : null
  const hp = live?.kind === 'point' ? points[live.i] : null

  return (
    <div className="minimap" ref={wrap} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <div className="minimap-stage"
        style={{ width: full, height: full, transform: `translate(${-ox}px, ${-oy}px)`, ['--k' as string]: view.span }}>
      {map.minimap_url && !imgFailed ? (
        <img className="minimap-img" src={map.minimap_url} alt={`${map.name} minimap`} onError={() => setFailedUrl(map.minimap_url)} draggable={false} />
      ) : (
        <div className="minimap-noimg">No minimap image for {map.name}</div>
      )}
      <svg className="minimap-svg under" viewBox={`0 0 ${VB} ${VB}`} aria-hidden="true">
        {showCalloutNames &&
          map.callouts.map((c) => (
            <text key={`${c.label}-${c.u}`} x={c.u * VB} y={c.v * VB} className="callout-name">
              {c.label}
            </text>
          ))}
      </svg>
      <canvas ref={canvas} className="minimap-canvas" style={{ width: full, height: full }} />
      <svg className="minimap-svg" viewBox={`0 0 ${VB} ${VB}`} role="img" aria-label={`${map.name} overlay`}>
        {bubbles.map((b) => {
          // Radii/labels are sized for the unzoomed frame; scale by span to keep them screen-constant.
          const r = bubbleRadius(b.weight) * view.span
          const t = TONE[b.tone ?? 'neutral']
          return (
            <g key={b.key}>
              <circle cx={b.u * VB} cy={b.v * VB} r={r} fill={t.fill} stroke={t.stroke} strokeWidth={2 * view.span} />
              {b.showLabel && (
                <text x={b.u * VB} y={b.v * VB - r - 8 * view.span} className="bubble-label">
                  <tspan x={b.u * VB} className="bubble-title">{b.title}</tspan>
                  <tspan x={b.u * VB} dy="1.15em" className="bubble-sub">{b.sub}</tspan>
                </text>
              )}
            </g>
          )
        })}
        {hp && (
          <circle cx={hp.u * VB} cy={hp.v * VB} r={11 * view.span} fill="none" stroke="#f0efec" strokeWidth={2.5 * view.span} />
        )}
      </svg>
      </div>
      {tip && live && (
        <div
          className="map-tip"
          style={{
            left: Math.min(live.x + 14, size - 240),
            top: live.y + 14 > size - 120 ? live.y - 110 : live.y + 14,
          }}
        >
          {tip}
        </div>
      )}
      {loading && <div className="map-loading"><span className="spinner" /> Loading…</div>}
      {!loading && empty && <div className="map-empty">{empty}</div>}
      {children}
    </div>
  )
}

/** Square uv window around the map's callouts (with margin), clamped inside the image. */
function fitView(map: MapInfo): { x0: number; y0: number; span: number } {
  const cs = map.callouts
  if (cs.length < 3) return { x0: 0, y0: 0, span: 1 }
  const us = cs.map((c) => c.u)
  const vs = cs.map((c) => c.v)
  const [u0, u1, v0, v1] = [Math.min(...us), Math.max(...us), Math.min(...vs), Math.max(...vs)]
  const span = Math.min(1, Math.max(0.35, Math.max(u1 - u0, v1 - v0) + 0.16))
  const clamp = (x: number) => Math.max(0, Math.min(1 - span, x))
  return { x0: clamp((u0 + u1) / 2 - span / 2), y0: clamp((v0 + v1) / 2 - span / 2), span }
}

function bubbleRadius(weight: number) {
  return 14 + 48 * Math.sqrt(Math.max(0, Math.min(1, weight)))
}
