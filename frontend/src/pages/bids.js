import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'

const BASE = 'https://ppc-optimaizer-production.up.railway.app'

function rub(n) { return !n ? '—' : Math.round(n).toLocaleString('ru') + ' ₽' }
function pos(n) { return (!n || n === 0) ? '—' : (Math.round(n * 10) / 10) }
function num(n) { return (!n && n !== 0) ? '—' : Math.round(n) }

function BidRecommendation({ current, recommended, reason }) {
  if (!recommended) return <span style={{ color: 'var(--text3)' }}>—</span>
  const diff = recommended - current
  const pct = current > 0 ? Math.round(diff / current * 100) : 0
  return (
    <div>
      <span style={{ fontWeight: 600, color: diff > 0 ? 'var(--green)' : 'var(--red)' }}>
        {rub(recommended)}
      </span>
      <span style={{ fontSize: 11, color: diff > 0 ? 'var(--green)' : 'var(--red)', marginLeft: 4 }}>
        {diff > 0 ? '▲' : '▼'}{Math.abs(pct)}%
      </span>
    </div>
  )
}

export default function Bids() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [analysis, setAnalysis] = useState(null)
  const [keywords, setKeywords] = useState([])
  const [campaigns, setCampaigns] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [hideAuto, setHideAuto] = useState(true)
  const [selCampaign, setSelCampaign] = useState('')
  const [cplTarget, setCplTarget] = useState(account?.target_cpl || 2000)
  const [crTarget, setCrTarget] = useState(10)

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    Promise.all([
      fetch(`${BASE}/api/v1/accounts/${accountId}/analyses`).then(r => r.json()),
      fetch(`${BASE}/api/v1/accounts/${accountId}/keywords`).then(r => r.json()),
      fetch(`${BASE}/api/v1/accounts/${accountId}/campaigns`).then(r => r.json()),
    ]).then(([analyses, kws, camps]) => {
      setAnalysis(analyses?.[0])
      setKeywords(Array.isArray(kws) ? kws : [])
      setCampaigns(Array.isArray(camps) ? camps : [])
    }).catch(console.error).finally(() => setLoading(false))
  }, [accountId])

  const problems = analysis?.problems || []
  const problemMap = {}
  problems.forEach(p => { if (p.keyword_id) problemMap[p.keyword_id] = p })

  const calcBid = () => Math.round(cplTarget * (crTarget / 100))

  const filtered = keywords.filter(kw => {
    if (hideAuto && kw.phrase?.includes('---autotargeting')) return false
    if (search && !kw.phrase?.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  const withProblems = filtered.map(kw => ({
    ...kw,
    _problem: problemMap[kw.id] || null,
  })).sort((a, b) => (b._problem ? 1 : 0) - (a._problem ? 1 : 0))

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Ставки</div>
      </div>

      {/* Фильтры */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          placeholder="Поиск по фразе..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ width: 200 }}
        />
        <select value={selCampaign} onChange={e => setSelCampaign(e.target.value)} className="btn" style={{ padding: '5px 10px' }}>
          <option value="">Все кампании</option>
          {campaigns.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <button
          className={`btn${hideAuto ? ' btn-primary' : ''}`}
          onClick={() => setHideAuto(h => !h)}
        >
          {hideAuto ? '✓' : ''} Скрыть автотаргетинг
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: 14 }}>
        {/* Таблица */}
        <div className="card" style={{ padding: 0, overflow: 'auto' }}>
          {loading ? (
            <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text3)' }}>Загрузка...</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Фраза</th>
                  <th>Тек. ставка</th>
                  <th>Рек. ставка</th>
                  <th>Причина</th>
                </tr>
              </thead>
              <tbody>
                {withProblems.length === 0 ? (
                  <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--text3)', padding: '2rem' }}>
                    Нет данных
                  </td></tr>
                ) : withProblems.slice(0, 300).map(kw => (
                  <tr key={kw.id}>
                    <td style={{ fontFamily: 'monospace', fontSize: 12, maxWidth: 280 }}>
                      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {kw.phrase}
                      </div>
                      {kw._problem && (
                        <div style={{ fontSize: 10, color: 'var(--red)', marginTop: 2 }}>
                          ⚠ {kw._problem.type === 'low_position' ? 'Низкая позиция'
                            : kw._problem.type === 'traffic_drop' ? 'Падение трафика'
                            : kw._problem.type === 'zero_ctr' ? 'Нет кликов'
                            : kw._problem.type}
                        </div>
                      )}
                    </td>
                    <td>{kw.current_bid ? rub(kw.current_bid) : '—'}</td>
                    <td>
                      <BidRecommendation
                        current={kw.current_bid}
                        recommended={kw._problem?.recommended_bid}
                        reason={kw._problem?.type}
                      />
                    </td>
                    <td style={{ fontSize: 11, color: 'var(--text2)', maxWidth: 200 }}>
                      {kw._problem?.description || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Калькулятор */}
        <div>
          <div className="card">
            <div className="card-title">Калькулятор ставки</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--text3)' }}>Целевой CPL, ₽</span>
                <input type="number" value={cplTarget} onChange={e => setCplTarget(Number(e.target.value))} />
              </label>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--text3)' }}>Целевой CR, %</span>
                <input type="number" value={crTarget} onChange={e => setCrTarget(Number(e.target.value))} />
              </label>
              <div style={{ background: 'var(--bg4)', borderRadius: 8, padding: '12px 14px' }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>Рекомендуемая ставка</div>
                <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--accent)' }}>
                  {rub(calcBid())}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
                  CPL {cplTarget}₽ × CR {crTarget}% = {calcBid()}₽
                </div>
              </div>
            </div>
          </div>

          {/* Статистика проблем */}
          <div className="card" style={{ marginTop: 12 }}>
            <div className="card-title">Требуют внимания</div>
            {problems.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>Проблем не найдено</div>
            ) : (
              <>
                {['critical', 'warning'].map(sev => {
                  const cnt = problems.filter(p => p.severity === sev).length
                  if (!cnt) return null
                  return (
                    <div key={sev} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '4px 0' }}>
                      <span className={`signal-badge ${sev}`}>
                        {sev === 'critical' ? '🔴 Критично' : '🟡 Важно'}
                      </span>
                      <span style={{ fontWeight: 600 }}>{cnt}</span>
                    </div>
                  )
                })}
              </>
            )}
          </div>
        </div>
      </div>
    </Layout>
  )
}
