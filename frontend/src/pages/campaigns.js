import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'

const BASE = 'https://ppc-optimaizer-production.up.railway.app'

function rub(n) { return !n ? '—' : Math.round(n).toLocaleString('ru') + ' ₽' }
function pct(n) { return !n ? '—' : (Math.round(n * 10) / 10) + '%' }
function num(n) { return (!n && n !== 0) ? '—' : Math.round(n).toLocaleString('ru') }
function pos(n) { return (!n || n === 0) ? '—' : (Math.round(n * 10) / 10) }

const METRICS = [
  { key: 'clicks', label: 'Клики', fmt: num },
  { key: 'impressions', label: 'Показы', fmt: num },
  { key: 'spend', label: 'Расход', fmt: rub },
  { key: 'ctr', label: 'CTR', fmt: pct },
  { key: 'avg_cpc', label: 'CPC', fmt: rub },
  { key: 'avg_position', label: 'Позиция', fmt: pos },
  { key: 'avg_click_position', label: 'Поз. клика', fmt: pos },
  { key: 'traffic_volume', label: 'Объём', fmt: num },
]

export default function Campaigns() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [campaigns, setCampaigns] = useState([])
  const [keywords, setKeywords] = useState([])
  const [view, setView] = useState('campaigns')
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [selCampaigns, setSelCampaigns] = useState([])

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    Promise.all([
      fetch(`${BASE}/api/v1/accounts/${accountId}/campaigns`).then(r => r.json()),
      fetch(`${BASE}/api/v1/accounts/${accountId}/keywords`).then(r => r.json()),
    ]).then(([c, k]) => {
      setCampaigns(Array.isArray(c) ? c : [])
      setKeywords(Array.isArray(k) ? k : [])
    }).catch(console.error).finally(() => setLoading(false))
  }, [accountId])

  const filteredKW = keywords.filter(kw => {
    if (search && !kw.phrase?.toLowerCase().includes(search.toLowerCase())) return false
    if (selCampaigns.length > 0) {
      // filter by selected campaigns if we had campaign_id on keywords
    }
    return true
  })

  const displayData = view === 'keywords' ? filteredKW : campaigns.filter(c =>
    !search || c.name?.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">По кампаниям</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            placeholder="Поиск..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ width: 180 }}
          />
          <div className="period-tabs">
            {['campaigns', 'keywords'].map(v => (
              <div key={v} className={`period-tab${view === v ? ' active' : ''}`} onClick={() => setView(v)}>
                {v === 'campaigns' ? 'Кампании' : 'Ключевые слова'}
              </div>
            ))}
          </div>
        </div>
      </div>

      {loading ? (
        <div style={{ color: 'var(--text3)', fontSize: 13 }}>Загрузка...</div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'auto' }}>
          {view === 'campaigns' ? (
            <table>
              <thead>
                <tr>
                  <th>Кампания</th>
                  <th>Тип</th>
                  <th>Стратегия</th>
                  <th>Статус</th>
                  <th>Групп</th>
                </tr>
              </thead>
              <tbody>
                {displayData.length === 0 ? (
                  <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--text3)', padding: '2rem' }}>
                    Нет данных — запустите сбор
                  </td></tr>
                ) : displayData.map(c => (
                  <tr key={c.id}>
                    <td style={{ fontWeight: 500, maxWidth: 300 }}>
                      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {c.name}
                      </div>
                    </td>
                    <td style={{ color: 'var(--text2)', fontSize: 12 }}>{c.campaign_type || '—'}</td>
                    <td>
                      <span className={`badge ${c.strategy_type === 'MANUAL_CPC' ? 'badge-ok' : 'badge-info'}`}>
                        {c.strategy_type === 'MANUAL_CPC' ? 'Ручная' : c.strategy_type || '—'}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${c.is_active ? 'badge-ok' : 'badge-warn'}`}>
                        {c.is_active ? 'Активна' : 'Остановлена'}
                      </span>
                    </td>
                    <td style={{ color: 'var(--text2)' }}>{c.ad_groups_count || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Ключевая фраза</th>
                  <th>Ставка</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {filteredKW.length === 0 ? (
                  <tr><td colSpan={3} style={{ textAlign: 'center', color: 'var(--text3)', padding: '2rem' }}>
                    Нет данных
                  </td></tr>
                ) : filteredKW.slice(0, 200).map(kw => (
                  <tr key={kw.id}>
                    <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{kw.phrase}</td>
                    <td>{kw.current_bid ? rub(kw.current_bid) : '—'}</td>
                    <td>
                      <span className={`badge ${kw.status === 'ACTIVE' ? 'badge-ok' : 'badge-warn'}`}>
                        {kw.status === 'ACTIVE' ? 'Активно' : kw.status || '—'}
                      </span>
                    </td>
                  </tr>
                ))}
                {filteredKW.length > 200 && (
                  <tr><td colSpan={3} style={{ textAlign: 'center', color: 'var(--text3)', fontSize: 12, padding: '1rem' }}>
                    Показано 200 из {filteredKW.length}
                  </td></tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      )}
    </Layout>
  )
}
