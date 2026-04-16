import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

function crColor(cr) {
  if (cr == null) return 'var(--text-3)'
  if (cr >= 15) return 'var(--green)'
  if (cr >= 5)  return 'var(--teal)'
  if (cr >= 3)  return 'var(--amber)'
  return 'var(--red)'
}

function crBadge(cr) {
  if (cr == null) return null
  if (cr >= 15) return 'badge-ok'
  if (cr >= 5)  return 'badge-info'
  if (cr >= 3)  return 'badge-warn'
  return 'badge-today'
}

export default function Keywords() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [keywords, setKeywords] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState('cr')

  useEffect(() => {
    if (!accountId) return
    api.getKeywords(accountId, '?limit=200')
      .then(data => { setKeywords(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [accountId])

  const filtered = keywords
    .filter(kw => !search || kw.phrase.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      const am = a.metrics, bm = b.metrics
      if (sortKey === 'cr') return (bm?.cr_click_lead ?? -1) - (am?.cr_click_lead ?? -1)
      if (sortKey === 'clicks') return (bm?.clicks ?? 0) - (am?.clicks ?? 0)
      if (sortKey === 'cpl') return (am?.cpl ?? 99999) - (bm?.cpl ?? 99999)
      return 0
    })

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.25rem' }}>
        <h1 style={{ fontSize: 20, fontWeight: 500 }}>Ключевые слова</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <input placeholder="Поиск по фразе..." value={search}
            onChange={e => setSearch(e.target.value)} style={{ width: 220 }} />
          <select value={sortKey} onChange={e => setSortKey(e.target.value)}>
            <option value="cr">По CR</option>
            <option value="clicks">По кликам</option>
            <option value="cpl">По CPL</option>
          </select>
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table>
          <thead>
            <tr>
              <th>Ключевая фраза</th>
              <th style={{ textAlign: 'right' }}>Клики</th>
              <th style={{ textAlign: 'right' }}>CR %</th>
              <th style={{ textAlign: 'right' }}>Заявки</th>
              <th style={{ textAlign: 'right' }}>CPL</th>
              <th style={{ textAlign: 'right' }}>Тек. ставка</th>
              <th style={{ textAlign: 'right' }}>Рек. ставка</th>
              <th>Значимость</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} style={{ textAlign: 'center', color: 'var(--text-3)', padding: '2rem' }}>
                Загрузка...
              </td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={8} style={{ textAlign: 'center', color: 'var(--text-3)', padding: '2rem' }}>
                Ключевых слов не найдено
              </td></tr>
            ) : filtered.map(kw => {
              const m = kw.metrics
              return (
                <tr key={kw.id}>
                  <td style={{ maxWidth: 280, fontFamily: 'monospace', fontSize: 12 }}
                    title={kw.phrase}>
                    {kw.phrase.length > 50 ? kw.phrase.slice(0, 50) + '…' : kw.phrase}
                  </td>
                  <td style={{ textAlign: 'right', fontWeight: 500 }}>{m?.clicks ?? '—'}</td>
                  <td style={{ textAlign: 'right' }}>
                    {m?.cr_click_lead != null
                      ? <span className={`badge ${crBadge(m.cr_click_lead)}`}>
                          {Math.round(m.cr_click_lead * 10) / 10}%
                        </span>
                      : <span style={{ color: 'var(--text-3)' }}>—</span>}
                  </td>
                  <td style={{ textAlign: 'right' }}>{m?.leads ?? '—'}</td>
                  <td style={{ textAlign: 'right', color: 'var(--text-2)' }}>
                    {m?.cpl ? Math.round(m.cpl).toLocaleString('ru-RU') + ' ₽' : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    {kw.current_bid ? Math.round(kw.current_bid) + ' ₽' : '—'}
                  </td>
                  <td style={{ textAlign: 'right', color: 'var(--purple)', fontWeight: 500 }}>
                    {m?.recommended_bid ? Math.round(m.recommended_bid) + ' ₽' : '—'}
                  </td>
                  <td>
                    {m?.is_significant
                      ? <span className="badge badge-ok">Значим</span>
                      : <span className="badge" style={{ background: 'var(--bg)', color: 'var(--text-3)' }}>
                          Копим данные
                        </span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Layout>
  )
}
