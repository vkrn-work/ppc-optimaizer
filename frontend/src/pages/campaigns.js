import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

export default function Campaigns() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [campaigns, setCampaigns] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!accountId) return
    api.getCampaigns(accountId)
      .then(data => { setCampaigns(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [accountId])

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <h1 style={{ fontSize: 20, fontWeight: 500, marginBottom: '1.25rem' }}>Кампании</h1>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table>
          <thead>
            <tr>
              <th>Название</th>
              <th>Тип</th>
              <th>Статус</th>
              <th>ID в Директе</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--text-3)', padding: '2rem' }}>Загрузка...</td></tr>
            ) : campaigns.length === 0 ? (
              <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--text-3)', padding: '2rem' }}>
                Кампании не найдены. Запустите сбор данных на дашборде.
              </td></tr>
            ) : campaigns.map(c => (
              <tr key={c.id}>
                <td style={{ fontWeight: 500 }}>{c.name}</td>
                <td>
                  <span className={`badge ${c.campaign_type === 'EPK' ? 'badge-warn' : 'badge-info'}`}>
                    {c.campaign_type || '—'}
                  </span>
                </td>
                <td>
                  <span className={`badge ${c.is_active ? 'badge-ok' : 'badge-bad'}`}>
                    {c.is_active ? 'Активна' : 'Остановлена'}
                  </span>
                </td>
                <td style={{ color: 'var(--text-3)', fontFamily: 'monospace', fontSize: 12 }}>
                  {c.direct_id}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Layout>
  )
}
