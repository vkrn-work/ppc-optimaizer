import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

function num(n, suffix = '') {
  if (n == null || n === 0) return '—'
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M' + suffix
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K' + suffix
  return Math.round(n * 10) / 10 + suffix
}
function rub(n) { return n == null ? '—' : Math.round(n).toLocaleString('ru-RU') + ' ₽' }
function pct(n) { return n == null ? '—' : (Math.round(n * 10) / 10) + '%' }
function pos(n) { return n == null || n === 0 ? '—' : (Math.round(n * 10) / 10).toString() }

const SEVERITY = {
  critical: { label: '🔴 Критично', color: 'var(--red)' },
  warning: { label: '🟡 Важно', color: 'var(--yellow, #f59e0b)' },
  info: { label: '🔵 Инфо', color: 'var(--blue)' },
}

const PRIORITY = {
  today: '🔴 Сегодня',
  this_week: '🟡 Эта неделя',
  month: '🔵 До конца месяца',
  scale: '🟢 Масштабирование',
}

export default function Dashboard() {
  const { account, accounts, accountId, switchAccount, loading } = useAccount()
  const [dash, setDash] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState('')
  const [expandedProblem, setExpandedProblem] = useState(null)

  useEffect(() => {
    if (!accountId) return
    api.getDashboard(accountId).then(setDash).catch(console.error)
  }, [accountId])

  async function handleSync() {
    if (!accountId) return
    setSyncing(true); setSyncMsg('')
    try {
      const r = await api.triggerSync(accountId)
      setSyncMsg(r.message || 'Сбор запущен')
      setTimeout(() => api.getDashboard(accountId).then(setDash), 15000)
    } catch (e) { setSyncMsg('Ошибка: ' + e.message) }
    finally { setSyncing(false) }
  }

  if (loading) return <div style={{ padding: 40, color: 'var(--text-3)' }}>Загрузка...</div>

  if (!account && accounts.length === 0) {
    return (
      <Layout account={null} accounts={[]} onAccountChange={switchAccount}>
        <div className="card" style={{ maxWidth: 500, margin: '4rem auto', textAlign: 'center' }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>🚀</div>
          <div style={{ fontWeight: 500, marginBottom: 8 }}>Добавьте рекламный кабинет</div>
          <p style={{ color: 'var(--text-2)', fontSize: 13 }}>Перейдите в Настройки и подключите кабинет Яндекс Директ</p>
          <a href="/settings" className="btn btn-primary" style={{ marginTop: 16 }}>Настройки →</a>
        </div>
      </Layout>
    )
  }

  const summary = dash?.last_analysis?.summary
  const problems = dash?.last_analysis?.problems || []
  const opportunities = dash?.last_analysis?.opportunities || []

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      {/* Заголовок */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 500, marginBottom: 4 }}>
            {account?.name || 'Дашборд'}
          </h1>
          {dash?.last_analysis?.created_at && (
            <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
              Последний анализ: {new Date(dash.last_analysis.created_at).toLocaleString('ru-RU')}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {syncMsg && <span style={{ fontSize: 12, color: 'var(--text-2)' }}>{syncMsg}</span>}
          <button className="btn btn-primary" onClick={handleSync} disabled={syncing}>
            {syncing ? '⏳ Сбор...' : '↻ Собрать данные'}
          </button>
        </div>
      </div>

      {!summary ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>📊</div>
          <div style={{ fontWeight: 500, marginBottom: 8 }}>Нет данных</div>
          <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16 }}>Нажмите «Собрать данные» чтобы запустить первый анализ</p>
          <button className="btn btn-primary" onClick={handleSync} disabled={syncing}>
            {syncing ? 'Запуск...' : '↻ Собрать данные'}
          </button>
        </div>
      ) : (
        <>
          {/* KPI блок */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: '1.5rem' }}>
            <KPI label="Клики" value={num(summary.total_clicks)} />
            <KPI label="Показы" value={num(summary.total_impressions)} />
            <KPI label="Расход" value={rub(summary.total_spend)} />
            <KPI label="CTR" value={pct(summary.ctr)} />
            <KPI label="Ср. CPC" value={rub(summary.avg_cpc)} />
            <KPI label="Ср. позиция" value={pos(summary.avg_position)} />
            <KPI label="Ключей" value={summary.keywords_analyzed} />
            <KPI label="Проблем" value={summary.problems_found} alert={summary.problems_found > 0} />
          </div>

          {/* Предложения сегодня */}
          {dash?.suggestions_stats?.urgent_today > 0 && (
            <div style={{
              background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
              borderRadius: 'var(--radius)', padding: '12px 16px', marginBottom: '1.5rem',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <span style={{ fontSize: 14 }}>
                🔴 <strong>{dash.suggestions_stats.urgent_today}</strong> предложений требуют внимания сегодня
              </span>
              <a href="/suggestions" className="btn" style={{ fontSize: 12 }}>Смотреть →</a>
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            {/* Проблемы */}
            <div className="card">
              <div style={{ fontWeight: 500, marginBottom: '1rem', display: 'flex', justifyContent: 'space-between' }}>
                <span>Проблемы ({problems.length})</span>
                {problems.length > 3 && <a href="/suggestions" style={{ fontSize: 12, color: 'var(--blue)' }}>Все →</a>}
              </div>
              {problems.length === 0 ? (
                <div style={{ color: 'var(--text-3)', fontSize: 13 }}>Проблем не обнаружено ✓</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {problems.slice(0, 5).map((p, i) => (
                    <div key={i} style={{
                      padding: '10px 12px', borderRadius: 'var(--radius)',
                      background: 'var(--bg)', cursor: 'pointer',
                      border: expandedProblem === i ? '1px solid var(--border-accent)' : '1px solid var(--border)',
                    }} onClick={() => setExpandedProblem(expandedProblem === i ? null : i)}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                        <div style={{ flex: 1 }}>
                          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4 }}>
                            <span style={{ fontSize: 11, color: SEVERITY[p.severity]?.color }}>
                              {SEVERITY[p.severity]?.label}
                            </span>
                            <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                              {PRIORITY[p.priority]}
                            </span>
                          </div>
                          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 2 }}>
                            {p.phrase}
                          </div>
                          <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
                            {p.description}
                          </div>
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--text-3)', marginLeft: 8, flexShrink: 0 }}>
                          {p.spend > 0 && rub(p.spend)}
                        </div>
                      </div>
                      {expandedProblem === i && (
                        <div style={{ marginTop: 8, padding: '8px 0', borderTop: '1px solid var(--border)' }}>
                          <div style={{ fontSize: 12, color: 'var(--green)', marginBottom: 4 }}>
                            💡 {p.action}
                          </div>
                          <div style={{ display: 'flex', gap: 12, fontSize: 11, color: 'var(--text-3)' }}>
                            {p.clicks != null && <span>Кликов: {p.clicks}</span>}
                            {p.avg_position != null && <span>Позиция: {p.avg_position}</span>}
                            {p.metric_value != null && p.type === 'traffic_drop' && <span>Падение: {p.metric_value}%</span>}
                            {p.recommended_bid && <span>Рек. ставка: {rub(p.recommended_bid)}</span>}
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Точки роста */}
            <div className="card">
              <div style={{ fontWeight: 500, marginBottom: '1rem' }}>
                Точки роста ({opportunities.length})
              </div>
              {opportunities.length === 0 ? (
                <div style={{ color: 'var(--text-3)', fontSize: 13 }}>Накапливается статистика...</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {opportunities.map((o, i) => (
                    <div key={i} style={{ padding: '10px 12px', borderRadius: 'var(--radius)', background: 'var(--bg)', border: '1px solid var(--border)' }}>
                      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>{o.phrase}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 4 }}>{o.description}</div>
                      <div style={{ fontSize: 12, color: 'var(--green)' }}>💡 {o.action}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Данные Метрики если есть */}
          {summary.has_metrika && (
            <div className="card" style={{ marginTop: 16 }}>
              <div style={{ fontWeight: 500, marginBottom: '1rem' }}>Поведение на сайте (Метрика)</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                <KPI label="Визиты" value={num(summary.metrika_visits)} />
                <KPI label="Отказы" value={pct(summary.metrika_bounce_rate)} />
                <KPI label="Глубина" value={summary.metrika_page_depth?.toFixed(1) || '—'} />
                <KPI label="Время на сайте" value={summary.metrika_avg_duration ? Math.round(summary.metrika_avg_duration) + ' с' : '—'} />
              </div>
            </div>
          )}
        </>
      )}
    </Layout>
  )
}

function KPI({ label, value, alert }) {
  return (
    <div className="card" style={{ textAlign: 'center', padding: '12px 8px' }}>
      <div style={{
        fontSize: 22, fontWeight: 600, marginBottom: 4,
        color: alert ? 'var(--red)' : 'var(--text-1)',
      }}>{value}</div>
      <div style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
    </div>
  )
}
