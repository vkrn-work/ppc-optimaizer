import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'

const BASE = 'https://ppc-optimaizer-production.up.railway.app'
function rub(n) { return !n ? '—' : Math.round(n).toLocaleString('ru') + ' ₽' }

export default function Negatives() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [queries, setQueries] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [dismissed, setDismissed] = useState(new Set())

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    fetch(`${BASE}/api/v1/accounts/${accountId}/search-queries?suggest=negatives`)
      .then(r => r.json())
      .then(d => setQueries(Array.isArray(d) ? d : []))
      .catch(() => setQueries([]))
      .finally(() => setLoading(false))
  }, [accountId])

  const filtered = queries.filter(q => {
    if (dismissed.has(q.id || q.query)) return false
    if (search && !q.query?.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div>
          <div className="page-title">Минуса</div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>
            Нерелевантные фразы для добавления в минус-слова
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        <input placeholder="Поиск..." value={search} onChange={e => setSearch(e.target.value)} style={{ width: 200 }} />
      </div>
      <div className="card" style={{ padding: 0 }}>
        {loading ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text3)' }}>Загрузка...</div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: '3rem', textAlign: 'center', color: 'var(--text3)' }}>
            <div style={{ fontSize: 24, marginBottom: 8 }}>✓</div>
            <div>Нет рекомендаций по минус-словам</div>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Поисковая фраза</th>
                <th>Ключ</th>
                <th>Клики</th>
                <th>Расход</th>
                <th>Тип проблемы</th>
                <th>Уровень минуса</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 100).map((q, i) => (
                <tr key={i}>
                  <td style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 500 }}>{q.query}</td>
                  <td style={{ fontSize: 12, color: 'var(--text2)' }}>{q.keyword_phrase || '—'}</td>
                  <td>{q.clicks || 0}</td>
                  <td>{rub(q.spend)}</td>
                  <td><span className="badge badge-bad">Нерелевантный</span></td>
                  <td>
                    <select className="btn" style={{ padding: '3px 8px', fontSize: 11 }}>
                      <option>Группа</option>
                      <option>Кампания</option>
                      <option>Аккаунт</option>
                    </select>
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-sm btn-danger">− В минуса</button>
                      <button className="btn btn-sm" onClick={() => setDismissed(s => new Set([...s, q.id || q.query]))}>
                        Пропустить
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Layout>
  )
}
