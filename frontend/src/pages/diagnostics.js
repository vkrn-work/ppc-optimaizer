import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'

const BASE = 'https://ppc-optimaizer-production.up.railway.app'

export default function Diagnostics() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${BASE}/health`)
      .then(r => r.json())
      .then(setHealth)
      .catch(() => setHealth({ status: 'error' }))
      .finally(() => setLoading(false))
  }, [])

  const checks = [
    { label: 'API бэкенда', ok: health?.status === 'ok', detail: health?.status === 'ok' ? 'Работает нормально' : 'Недоступен' },
    { label: 'База данных', ok: health?.db !== 'error', detail: health?.db || 'Неизвестно' },
    { label: 'Токен Директа', ok: !!account?.oauth_token, detail: account?.oauth_token ? 'Настроен' : 'Не настроен' },
    { label: 'Метрика', ok: !!account?.metrika_counter_id, detail: account?.metrika_counter_id ? `Счётчик ${account.metrika_counter_id}` : 'Не настроена' },
    { label: 'Последний сбор', ok: !!account?.last_sync_at, detail: account?.last_sync_at ? new Date(account.last_sync_at).toLocaleString('ru-RU') : 'Никогда' },
  ]

  const hasErrors = checks.some(c => !c.ok)

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Диагностика</div>
        {hasErrors && <span className="badge badge-bad">⚠ Есть проблемы</span>}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 600 }}>
        {loading ? (
          <div style={{ color: 'var(--text3)' }}>Проверка...</div>
        ) : checks.map((c, i) => (
          <div key={i} className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: c.ok ? 'var(--green)' : 'var(--red)', flexShrink: 0 }} />
              <div>
                <div style={{ fontWeight: 500, fontSize: 13 }}>{c.label}</div>
                <div style={{ fontSize: 12, color: 'var(--text3)' }}>{c.detail}</div>
              </div>
            </div>
            <span className={`badge ${c.ok ? 'badge-ok' : 'badge-bad'}`}>{c.ok ? 'OK' : 'Ошибка'}</span>
          </div>
        ))}
      </div>
    </Layout>
  )
}
