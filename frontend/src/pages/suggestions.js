import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const TYPES = {
  low_position: 'Позиция',
  traffic_drop: 'Трафик',
  zero_ctr: 'CTR',
  low_ctr: 'CTR',
  click_position_gap: 'Позиция клика',
  scale_high_ctr: 'Масштаб',
  high_cr: 'CR',
}

const PRIORITY_OPTS = [
  { value: '', label: 'Все приоритеты' },
  { value: 'today', label: '🔴 Сегодня' },
  { value: 'this_week', label: '🟡 Неделя' },
  { value: 'month', label: '🔵 Месяц' },
]

const SEV_OPTS = [
  { value: '', label: 'Все' },
  { value: 'critical', label: 'Критично' },
  { value: 'warning', label: 'Важно' },
  { value: 'success', label: 'Рост' },
]

function rub(n) { return n == null ? '—' : Math.round(n).toLocaleString('ru') + ' ₽' }

export default function Suggestions() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [analysis, setAnalysis] = useState(null)
  const [filterPri, setFilterPri] = useState('')
  const [filterSev, setFilterSev] = useState('')
  const [filterType, setFilterType] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    fetch(`https://ppc-optimaizer-production.up.railway.app/api/v1/accounts/${accountId}/analyses`)
      .then(r => r.json())
      .then(data => {
        if (data && data.length > 0) setAnalysis(data[0])
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [accountId])

  const allItems = [
    ...(analysis?.problems || []).map(p => ({ ...p, _cat: 'problem' })),
    ...(analysis?.opportunities || []).map(o => ({ ...o, _cat: 'opportunity', severity: 'success' })),
  ]

  const filtered = allItems.filter(item => {
    if (filterPri && item.priority !== filterPri) return false
    if (filterSev && item.severity !== filterSev) return false
    if (filterType && item.type !== filterType) return false
    return true
  })

  const types = [...new Set(allItems.map(i => i.type).filter(Boolean))]

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">
          Предложения
          {allItems.length > 0 && (
            <span style={{ fontSize: 13, color: 'var(--text3)', fontWeight: 400, marginLeft: 8 }}>
              {allItems.length} всего
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <select className="btn" value={filterSev} onChange={e => setFilterSev(e.target.value)} style={{ padding: '5px 10px' }}>
            {SEV_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <select className="btn" value={filterPri} onChange={e => setFilterPri(e.target.value)} style={{ padding: '5px 10px' }}>
            {PRIORITY_OPTS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <select className="btn" value={filterType} onChange={e => setFilterType(e.target.value)} style={{ padding: '5px 10px' }}>
            <option value="">Все типы</option>
            {types.map(t => <option key={t} value={t}>{TYPES[t] || t}</option>)}
          </select>
        </div>
      </div>

      {loading ? (
        <div style={{ color: 'var(--text3)', fontSize: 13 }}>Загрузка...</div>
      ) : filtered.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-icon">◈</div>
            <div className="empty-title">Предложений нет</div>
            <div className="empty-desc">Запустите сбор данных чтобы получить рекомендации</div>
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {filtered.map((item, i) => {
            const sev = item.severity || 'warning'
            return (
              <div key={i} className={`signal-item ${sev}`} style={{ cursor: 'default' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div style={{ flex: 1 }}>
                    <div className="signal-header">
                      <span className={`signal-badge ${sev}`}>
                        {sev === 'critical' ? '🔴 Критично' : sev === 'warning' ? '🟡 Важно' : '🟢 Рост'}
                      </span>
                      {item.priority && (
                        <span style={{ fontSize: 10, color: 'var(--text3)' }}>
                          {item.priority === 'today' ? '🔴 Сегодня' : item.priority === 'this_week' ? '🟡 Неделя' : '🔵 Месяц'}
                        </span>
                      )}
                      {item.type && (
                        <span style={{ fontSize: 10, background: 'var(--bg3)', color: 'var(--text3)', padding: '1px 5px', borderRadius: 3 }}>
                          {TYPES[item.type] || item.type}
                        </span>
                      )}
                    </div>
                    <div className="signal-phrase">{item.phrase}</div>
                    <div className="signal-desc">{item.description}</div>
                    <div className="signal-action">→ {item.action}</div>
                    <div style={{ display: 'flex', gap: 12, marginTop: 6, fontSize: 11, color: 'var(--text3)' }}>
                      {item.clicks != null && <span>Кликов: <b style={{ color: 'var(--text1)' }}>{item.clicks}</b></span>}
                      {item.avg_position != null && <span>Позиция: <b style={{ color: 'var(--text1)' }}>{item.avg_position}</b></span>}
                      {item.spend > 0 && <span>Расход: <b style={{ color: 'var(--text1)' }}>{rub(item.spend)}</b></span>}
                      {item.recommended_bid && <span>Рек. ставка: <b style={{ color: 'var(--accent)' }}>{rub(item.recommended_bid)}</b></span>}
                    </div>
                  </div>
                  <button className="btn btn-sm btn-primary" style={{ marginLeft: 12, flexShrink: 0 }}>
                    В работу →
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Layout>
  )
}
