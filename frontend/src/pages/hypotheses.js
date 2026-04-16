import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const VERDICT_META = {
  confirmed:   { label: 'Подтверждена', cls: 'badge-ok' },
  rejected:    { label: 'Отклонена',    cls: 'badge-today' },
  neutral:     { label: 'Нейтральная',  cls: 'badge-week' },
  insufficient:{ label: 'Мало данных',  cls: 'badge-info' },
}

export default function Hypotheses() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [hypotheses, setHypotheses] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!accountId) return
    api.getHypotheses(accountId)
      .then(data => { setHypotheses(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [accountId])

  const active = hypotheses.filter(h => !h.verdict)
  const done = hypotheses.filter(h => h.verdict)

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <h1 style={{ fontSize: 20, fontWeight: 500, marginBottom: '0.5rem' }}>Гипотезы</h1>
      <p style={{ fontSize: 12, color: 'var(--text-3)', marginBottom: '1.5rem' }}>
        7-дневный трекинг каждого изменения. Система автоматически сравнивает метрики до и после.
      </p>

      {active.length > 0 && (
        <div style={{ marginBottom: '1.5rem' }}>
          <div style={{ fontWeight: 500, marginBottom: 10, fontSize: 13, color: 'var(--text-2)' }}>
            Активные — {active.length}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {active.map(h => (
              <div key={h.id} className="card" style={{ padding: '1rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div>
                    <span className="badge badge-info" style={{ marginBottom: 6 }}>Трекинг активен</span>
                    <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}>
                      Применено: {new Date(h.applied_at).toLocaleString('ru-RU')}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
                      Вердикт: {new Date(h.track_until).toLocaleString('ru-RU')}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right', fontSize: 12, color: 'var(--text-3)' }}>
                    Осталось: {Math.max(0, Math.ceil(
                      (new Date(h.track_until) - new Date()) / 86400000
                    ))} дн.
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ fontWeight: 500, marginBottom: 10, fontSize: 13, color: 'var(--text-2)' }}>
        Завершённые — {done.length}
      </div>

      {loading ? (
        <div style={{ color: 'var(--text-3)' }}>Загрузка...</div>
      ) : done.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-3)', padding: '2rem' }}>
          Пока нет завершённых гипотез. Они появятся через 7 дней после одобрения первого предложения.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {done.map(h => {
            const v = VERDICT_META[h.verdict] || { label: h.verdict, cls: 'badge-info' }
            return (
              <div key={h.id} className="card" style={{ padding: '1rem' }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                  <span className={`badge ${v.cls}`}>{v.label}</span>
                  {h.delta_percent != null && (
                    <span style={{
                      fontSize: 13, fontWeight: 500,
                      color: h.delta_percent >= 10 ? 'var(--green)' : h.delta_percent <= -10 ? 'var(--red)' : 'var(--text-2)',
                    }}>
                      {h.delta_percent > 0 ? '+' : ''}{Math.round(h.delta_percent * 10) / 10}%
                    </span>
                  )}
                </div>
                <p style={{ fontSize: 13, color: 'var(--text-2)', marginBottom: 8 }}>{h.report}</p>
                {h.metrics_before && h.metrics_after && (
                  <div style={{ display: 'flex', gap: 24, fontSize: 12, color: 'var(--text-3)' }}>
                    <span>До: {h.metrics_before.clicks} кл.</span>
                    <span>После: {h.metrics_after.clicks} кл.</span>
                    <span>Применено: {new Date(h.applied_at).toLocaleDateString('ru-RU')}</span>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </Layout>
  )
}
