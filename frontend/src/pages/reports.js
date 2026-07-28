import { useState, useEffect, useMemo } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'
import DateRangePicker from '../components/DateRangePicker'

// v1.7.0 — «Конструктор отчётов» (пункт 2): аналог Мастера отчётов Директа /
// отчётов Roistat. Данные приходят одним плоским набором из GET /report
// (см. backend/app/api/routes.py), группировка задаётся на бэкенде (group_by),
// а какие столбцы показывать — решает пользователь через
// модалку настроек (сохраняется в localStorage, как и тема в Layout.js).

const GROUPS = [
  { key: 'campaign', label: 'По кампаниям' },
  { key: 'ad_group', label: 'По группам' },
  { key: 'keyword',  label: 'По ключам' },
  { key: 'date',     label: 'По дням' },
]

// Полный каталог доступных столбцов. `groups: null` — показывать при любой
// группировке, иначе только при перечисленных.
const ALL_COLUMNS = [
  { key: 'campaign_name',  label: 'Кампания',        groups: null,               type: 'text' },
  { key: 'strategy_type',  label: 'Стратегия',       groups: ['campaign'],       type: 'strategy' },
  { key: 'ad_group_name',  label: 'Группа',          groups: ['ad_group','keyword'], type: 'text' },
  { key: 'keyword_phrase', label: 'Ключевая фраза',  groups: ['keyword'],        type: 'text' },
  { key: 'current_bid',    label: 'Текущая ставка',  groups: ['keyword'],        type: 'rub' },
  { key: 'date',           label: 'Дата',            groups: ['date'],           type: 'text' },
  { key: 'impressions',    label: 'Показы',          groups: null,               type: 'num', default: true },
  { key: 'clicks',         label: 'Клики',           groups: null,               type: 'num', default: true },
  { key: 'ctr',            label: 'CTR',             groups: null,               type: 'pct', default: true },
  { key: 'spend',          label: 'Расход',          groups: null,               type: 'rub', default: true },
  { key: 'avg_cpc',        label: 'CPC',             groups: null,               type: 'rub', default: true },
  { key: 'avg_position',   label: 'Поз. показа',     groups: null,               type: 'num' },
  { key: 'avg_click_position', label: 'Поз. клика',  groups: null,               type: 'num' },
  { key: 'traffic_volume', label: 'Объём трафика',   groups: null,               type: 'num' },
  { key: 'weighted_ctr',   label: 'Взвеш. CTR',      groups: null,               type: 'pct' },
  { key: 'bounce_rate',    label: 'Отказы',          groups: null,               type: 'pct' },
  { key: 'sessions',       label: 'Визиты (Метрика)',groups: null,               type: 'num' },
  { key: 'leads',          label: 'Заявки',          groups: null,               type: 'num', default: true },
  { key: 'mql',            label: 'MQL',             groups: null,               type: 'num' },
  { key: 'sql',            label: 'SQL (БП)',        groups: null,               type: 'num', default: true },
  { key: 'cr_lead_mql',    label: 'CR заявка→MQL',   groups: null,               type: 'pct' },
  { key: 'cr_mql_sql',     label: 'CR MQL→SQL',     groups: null,               type: 'pct' },
  { key: 'cpl',            label: 'CPL',             groups: null,               type: 'rub', default: true },
  { key: 'cost_per_mql',   label: 'Цена MQL',        groups: null,               type: 'rub' },
  { key: 'cost_per_sql',   label: 'Цена SQL (CPQL)', groups: null,               type: 'rub', default: true },
]

const STORAGE_KEY = 'ppc_report_columns_v1'

function loadVisibleColumns() {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch (e) {}
  return null
}

function fmt(type, v) {
  if (v == null || v === '') return '—'
  if (type === 'rub') return Math.round(v).toLocaleString('ru') + ' ₽'
  if (type === 'pct') return (Math.round(v * 10) / 10) + '%'
  if (type === 'num') return v >= 1000 ? Math.round(v).toLocaleString('ru') : (Math.round(v * 10) / 10)
  if (type === 'strategy') return v === 'MANUAL_CPC' ? 'Ручная' : (v ? 'Авто' : '—')
  return v
}

function ColumnSettingsModal({ visible, onClose, groupBy, checked, onToggle }) {
  if (!visible) return null
  const available = ALL_COLUMNS.filter(c => !c.groups || c.groups.includes(groupBy))
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
    }} onClick={onClose}>
      <div className="card" style={{ width: 420, maxHeight: '80vh', overflow: 'auto', padding: 20 }}
        onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
          <div style={{ fontWeight: 600, fontSize: 15 }}>Настроить отчёт</div>
          <button className="btn btn-sm" onClick={onClose}>✕</button>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
          Выберите столбцы, которые нужно показать в таблице (для текущей группировки).
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {available.map(c => (
            <label key={c.key} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
              <input type="checkbox" checked={!!checked[c.key]} onChange={() => onToggle(c.key)} />
              {c.label}
            </label>
          ))}
        </div>
      </div>
    </div>
  )
}

// v1.7.6: единый выбор периода (пункт 3). По умолчанию — последние 30 дней.
function _defaultRange() {
  const z = n => String(n).padStart(2, '0')
  const fmt = d => `${d.getFullYear()}-${z(d.getMonth()+1)}-${z(d.getDate())}`
  const to = new Date(); const from = new Date(); from.setDate(from.getDate() - 29)
  return { from: fmt(from), to: fmt(to) }
}

export default function Reports() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [groupBy, setGroupBy]     = useState('campaign')
  const [range, setRange]         = useState(_defaultRange)
  const [campaigns, setCampaigns] = useState([])
  const [selCampaign, setSelCampaign] = useState('')
  const [activeOnly, setActiveOnly]   = useState(true)
  const [data, setData]           = useState(null)
  const [loading, setLoading]     = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [visibleCols, setVisibleCols] = useState(() => {
    const saved = loadVisibleColumns()
    if (saved) return saved
    const init = {}
    ALL_COLUMNS.forEach(c => { init[c.key] = !!c.default })
    return init
  })

  useEffect(() => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(visibleCols))
    }
  }, [visibleCols])

  useEffect(() => {
    if (!accountId) return
    api.getCampaigns(accountId, 'month', false).then(list => setCampaigns(Array.isArray(list) ? list : [])).catch(() => {})
  }, [accountId])

  function load() {
    if (!accountId) return
    setLoading(true)
    const params = new URLSearchParams({ group_by: groupBy, active_only: activeOnly ? 'true' : 'false' })
    if (range?.from && range?.to) {
      params.set('date_from', range.from)
      params.set('date_to', range.to)
    }
    if (selCampaign) params.set('campaign_id', selCampaign)
    api.getReport(accountId, `?${params.toString()}`)
      .then(setData)
      .catch(e => { console.error(e); setData(null) })
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [accountId, groupBy, range, selCampaign, activeOnly])

  function toggleColumn(key) {
    setVisibleCols(prev => ({ ...prev, [key]: !prev[key] }))
  }

  const columns = useMemo(
    () => ALL_COLUMNS.filter(c => (!c.groups || c.groups.includes(groupBy)) && visibleCols[c.key]),
    [groupBy, visibleCols]
  )

  function exportCsv() {
    if (!data?.rows?.length) return
    const header = columns.map(c => c.label).join(';')
    const lines = data.rows.map(r => columns.map(c => {
      const v = r[c.key]
      return v == null ? '' : String(v).replace(/;/g, ',')
    }).join(';'))
    const csv = [header, ...lines].join('\n')
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `report_${groupBy}_${range.from}_${range.to}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Конструктор отчётов</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-sm" onClick={() => setModalOpen(true)}>⚙ Настроить отчёт</button>
          <button className="btn btn-sm" onClick={exportCsv} disabled={!data?.rows?.length}>⬇ Экспорт CSV</button>
          <button className="btn btn-sm" onClick={load}>{loading ? '⏳' : '↻'}</button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <div className="period-tabs" style={{ display: 'inline-flex' }}>
          {GROUPS.map(g => (
            <div key={g.key} className={`period-tab${groupBy===g.key?' active':''}`} onClick={()=>setGroupBy(g.key)}>{g.label}</div>
          ))}
        </div>
        <DateRangePicker value={range} onChange={setRange} />
        <select value={selCampaign} onChange={e => setSelCampaign(e.target.value)} style={{ padding: '6px 10px', fontSize: 12 }}>
          <option value="">Все кампании</option>
          {campaigns.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
          <input type="checkbox" checked={activeOnly} onChange={e => setActiveOnly(e.target.checked)} />
          Только активные
        </label>
        {data?.row_count != null && (
          <div style={{ fontSize: 12, color: 'var(--text3)' }}>
            {data.row_count} строк · {data.period_dates?.from} — {data.period_dates?.to}
          </div>
        )}
      </div>

      {data?.attribution && (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 10, fontSize: 12 }}>
          <div style={{ color: 'var(--text3)' }}>
            Заявок в CRM за период: <b>{data.attribution.leads_total_crm}</b>,
            в отчёте: <b>{data.attribution.leads_in_report}</b>
            {data.attribution.leads_unattributed != null && data.attribution.leads_unattributed > 0 && (
              <span style={{ color: 'var(--warn, #b45309)' }}> · не разнесено: {data.attribution.leads_unattributed}</span>
            )}
          </div>
          {data.attribution.campaign_spend_source === 'keyword_stats_fallback' && (
            <div style={{ color: 'var(--warn, #b45309)' }}>
              ⚠ Расход РСЯ может быть занижен — не собрана статистика кампаний, запустите синхронизацию
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ padding: 0, overflow: 'auto' }}>
        <table>
          <thead>
            <tr>
              {columns.map(c => <th key={c.key}>{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={columns.length} style={{ textAlign: 'center', padding: 20, color: 'var(--text3)' }}>Загрузка...</td></tr>
            ) : !data?.rows?.length ? (
              <tr><td colSpan={columns.length} style={{ textAlign: 'center', padding: 20, color: 'var(--text3)' }}>Нет данных за этот период</td></tr>
            ) : data.rows.map((r, i) => (
              <tr key={i}>
                {columns.map(c => (
                  <td key={c.key} style={c.type === 'text' ? { maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } : {}}>
                    {fmt(c.type, r[c.key])}
                    {c.key === 'campaign_name' && r.no_keywords && (
                      <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--text3)', border: '1px solid var(--border)', borderRadius: 4, padding: '0 4px' }}>РСЯ/сеть</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          {data?.totals && data.rows?.length > 0 && (
            <tfoot>
              <tr style={{ fontWeight: 600, background: 'var(--bg4)' }}>
                {columns.map((c, i) => (
                  <td key={c.key}>{i === 0 ? 'Итого' : fmt(c.type, data.totals[c.key])}</td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      <ColumnSettingsModal
        visible={modalOpen}
        onClose={() => setModalOpen(false)}
        groupBy={groupBy}
        checked={visibleCols}
        onToggle={toggleColumn}
      />
    </Layout>
  )
}
