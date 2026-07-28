import { useState, useRef, useEffect } from 'react'

// v1.7.6: единый выбор периода для всех отчётных вкладок (пункт 3 запроса).
// Одно поле — внутри и быстрые пресеты (7/30/90/365 дней, месяц, квартал,
// год), и точный диапазон «от — до». Возвращает {from, to} в формате
// YYYY-MM-DD; бэкенд (period_dates) приоритезирует явные даты над preset,
// поэтому пресеты тоже разворачиваются в конкретные даты.

function iso(d) {
  const z = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`
}
function ru(s) {
  if (!s) return ''
  const [y, m, d] = s.split('-')
  return `${d}.${m}.${y}`
}
function daysAgo(n) {
  const d = new Date(); d.setDate(d.getDate() - n); return d
}

// Пресеты возвращают {from, to} как Date-объекты.
function presetRange(key) {
  const today = new Date()
  const start = new Date(today)
  switch (key) {
    case 'today':    return { from: today, to: today }
    case 'yesterday':{ const y = daysAgo(1); return { from: y, to: y } }
    case '7d':       return { from: daysAgo(6),  to: today }
    case '30d':      return { from: daysAgo(29), to: today }
    case '90d':      return { from: daysAgo(89), to: today }
    case '365d':     return { from: daysAgo(364), to: today }
    case 'this_month': return { from: new Date(today.getFullYear(), today.getMonth(), 1), to: today }
    case 'prev_month': {
      const f = new Date(today.getFullYear(), today.getMonth() - 1, 1)
      const t = new Date(today.getFullYear(), today.getMonth(), 0)
      return { from: f, to: t }
    }
    case 'quarter': {
      const q = Math.floor(today.getMonth() / 3)
      return { from: new Date(today.getFullYear(), q * 3, 1), to: today }
    }
    case 'year':     return { from: new Date(today.getFullYear(), 0, 1), to: today }
    default:         return { from: start, to: today }
  }
}

const PRESETS = [
  { key: 'today',      label: 'Сегодня' },
  { key: 'yesterday',  label: 'Вчера' },
  { key: '7d',         label: '7 дней' },
  { key: '30d',        label: '30 дней' },
  { key: '90d',        label: '90 дней' },
  { key: '365d',       label: '365 дней' },
  { key: 'this_month', label: 'Этот месяц' },
  { key: 'prev_month', label: 'Прошлый месяц' },
  { key: 'quarter',    label: 'Квартал' },
  { key: 'year',       label: 'Год' },
]

export default function DateRangePicker({ value, onChange }) {
  const [open, setOpen] = useState(false)
  const [from, setFrom] = useState(value?.from || '')
  const [to, setTo]     = useState(value?.to || '')
  const ref = useRef(null)

  useEffect(() => { setFrom(value?.from || ''); setTo(value?.to || '') }, [value?.from, value?.to])

  useEffect(() => {
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  function applyPreset(key) {
    const r = presetRange(key)
    const nf = iso(r.from), nt = iso(r.to)
    setFrom(nf); setTo(nt)
    onChange({ from: nf, to: nt })
    setOpen(false)
  }
  function applyManual() {
    if (from && to) { onChange({ from, to }); setOpen(false) }
  }

  const label = value?.from && value?.to ? `${ru(value.from)} — ${ru(value.to)}` : 'Выбрать период'

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
      <button className="btn btn-sm" onClick={() => setOpen(o => !o)}
        style={{ minWidth: 200, textAlign: 'left' }}>
        📅 {label}
      </button>
      {open && (
        <div className="card" style={{
          position: 'absolute', top: '110%', left: 0, zIndex: 1000, width: 320,
          padding: 14, boxShadow: '0 8px 24px rgba(0,0,0,0.15)',
        }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
            {PRESETS.map(p => (
              <div key={p.key} onClick={() => applyPreset(p.key)}
                style={{
                  padding: '4px 10px', fontSize: 12, borderRadius: 6, cursor: 'pointer',
                  border: '1px solid var(--border)', background: 'var(--bg2)',
                }}>{p.label}</div>
            ))}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>Или точный диапазон:</div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 10 }}>
            <input type="date" value={from} max={to || undefined}
              onChange={e => setFrom(e.target.value)}
              style={{ padding: '5px 8px', fontSize: 12, flex: 1 }} />
            <span style={{ color: 'var(--text3)' }}>—</span>
            <input type="date" value={to} min={from || undefined}
              onChange={e => setTo(e.target.value)}
              style={{ padding: '5px 8px', fontSize: 12, flex: 1 }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6 }}>
            <button className="btn btn-sm" onClick={() => setOpen(false)}>Отмена</button>
            <button className="btn btn-sm btn-primary" onClick={applyManual}
              disabled={!from || !to}>Применить</button>
          </div>
        </div>
      )}
    </div>
  )
}
