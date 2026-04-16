import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'

const PRIORITY_LABELS = {
  today: { label: 'Сегодня', cls: 'badge-today' },
  this_week: { label: 'Эта неделя', cls: 'badge-week' },
  month: { label: 'До конца месяца', cls: 'badge-month' },
  scale: { label: 'Масштабирование', cls: 'badge-scale' },
}

function fmt(n) {
  if (n == null) return '—'
  if (n >= 1000) return Math.round(n).toLocaleString('ru-RU') + ' ₽'
  return Math.round(n) + ' ₽'
}
function pct(n) { return n == null ? '—' : Math.round(n * 10) / 10 + '%' }

export default function Dashboard() {
  const { account, accounts, accountId, switchAccount, loading } = useAccount()
  const [dash, setDash] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState('')

  useEffect(() => {
    if (!accountId) return
    api.getDashboard(accountId).then(setDash).catch(console.error)
  }, [accountId])

  async function handleSync() {
    if (!accountId) return
    setSyncing(true)
    setSyncMsg('')
    try {
      const r = await api.triggerSync(accountId)
      setSyncMsg(r.message)
    } catch (e) {
      setSyncMsg('Ошибка: ' + e.message)
    } finally {
      setSyncing(false)
    }
  }

  if (loading) return <div style={{ padding: 40, color: 'var(--text-3)' }}>Загрузка...</div>

  if (!account && accounts.length === 0) {
    return (
      <Layout account={null} accounts={[]} onAccountChange={switchAccount}>
        <NoAccountScreen />
      </Layout>
    )
  }

  const summary = dash?.last_analysis?.summary
  const problems = dash?.last_analysis?.problems || []
  const opportunities = dash?.last_analysis?.opportunities || []

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 500 }}>{account?.name || 'Дашборд'}</h1>
          <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 2 }}>
            {account?.last_sync_at
              ? `Последний сбор: ${new Date(account.last_sync_at).toLocaleString('ru-RU')}`
              : 'Данные ещё не собирались'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {syncMsg && <span style={{ fontSize: 12, color: 'var(--text-2)' }}>{syncMsg}</span>}
          <button className="btn btn-primary" onClick={handleSync} disabled={syncing}>
            {syncing ? '⟳ Запущено...' : '↻ Собрать данные'}
          </button>
        </div>
      </div>

      {/* KPI Grid */}
      {summary ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: '1.5rem' }}>
          <StatCard label="Клики (4 нед.)" value={summary.total_clicks?.toLocaleString('ru-RU')} unit="" />
          <StatCard label="CR клик→заявка" value={pct(summary.cr_click_lead)} unit=""
            status={summary.cr_click_lead >= 5 ? 'ok' : summary.cr_click_lead >= 3 ? 'warn' : 'bad'} />
          <StatCard label="CPL" value={fmt(summary.cpl)}
            status={summary.cpl && account?.target_cpl
              ? (summary.cpl <= account.target_cpl ? 'ok' : summary.cpl <= account.target_cpl * 1.5 ? 'warn' : 'bad')
              : null} />
          <StatCard label="Заявки (4 нед.)" value={summary.total_leads} unit="" />
        </div>
      ) : (
        <EmptyState text="Нет данных анализа. Нажмите «Собрать данные» для запуска." />
      )}

      {/* Suggestions alert */}
      {dash?.suggestions_stats?.urgent_today > 0 && (
        <div style={{
          background: 'var(--red-bg)',
          border: '0.5px solid var(--red)',
          borderRadius: 'var(--radius)',
          padding: '0.75rem 1rem',
          marginBottom: '1rem',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}>
          <span style={{ color: 'var(--red)', fontWeight: 500 }}>
            {dash.suggestions_stats.urgent_today} задач на сегодня — требуют немедленного внимания
          </span>
          <a href="/suggestions?priority=today" className="btn" style={{ fontSize: 12, padding: '4px 10px' }}>
            Смотреть →
          </a>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
        {/* Problems */}
        <div className="card">
          <div style={{ fontWeight: 500, marginBottom: '1rem', display: 'flex', justifyContent: 'space-between' }}>
            <span>Топ проблем</span>
            <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{problems.length}</span>
          </div>
          {problems.length === 0
            ? <div style={{ color: 'var(--text-3)', fontSize: 13 }}>Критичных проблем не обнаружено</div>
            : problems.map((p, i) => (
              <div key={i} style={{
                padding: '10px 0',
                borderBottom: i < problems.length - 1 ? '0.5px solid var(--border)' : 'none',
              }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 4 }}>
                  <span className={`badge ${p.severity === 'critical' ? 'badge-today' : 'badge-warn'}`}>
                    {p.severity === 'critical' ? 'Критично' : 'Внимание'}
                  </span>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>{p.phrase}</span>
                </div>
                <p style={{ fontSize: 12, color: 'var(--text-2)', margin: '4px 0' }}>{p.description}</p>
                <p style={{ fontSize: 12, color: 'var(--text-3)' }}>{p.action}</p>
              </div>
            ))}
        </div>

        {/* Opportunities */}
        <div className="card">
          <div style={{ fontWeight: 500, marginBottom: '1rem', display: 'flex', justifyContent: 'space-between' }}>
            <span>Точки роста</span>
            <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{opportunities.length}</span>
          </div>
          {opportunities.length === 0
            ? <div style={{ color: 'var(--text-3)', fontSize: 13 }}>Пока нет данных для масштабирования</div>
            : opportunities.map((o, i) => (
              <div key={i} style={{
                padding: '10px 0',
                borderBottom: i < opportunities.length - 1 ? '0.5px solid var(--border)' : 'none',
              }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                  <span className="badge badge-ok">CR {o.cr}%</span>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>{o.phrase}</span>
                </div>
                <p style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 2 }}>{o.description}</p>
                <p style={{ fontSize: 12, color: 'var(--teal)', fontWeight: 500 }}>{o.action}</p>
              </div>
            ))}
        </div>
      </div>

      {/* Quick stats */}
      {summary && (
        <div className="card" style={{ marginTop: '1.25rem' }}>
          <div style={{ fontWeight: 500, marginBottom: '1rem' }}>Сводка за 4 недели</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 16 }}>
            <MiniStat label="Расход" value={`${Math.round(summary.total_spend).toLocaleString('ru-RU')} ₽`} />
            <MiniStat label="Заявки" value={summary.total_leads} />
            <MiniStat label="SQL" value={summary.total_sqls} />
            <MiniStat label="CPQL" value={fmt(summary.cpql)} />
            <MiniStat label="Ключей" value={summary.keywords_analyzed} />
          </div>
        </div>
      )}
    </Layout>
  )
}

function StatCard({ label, value, unit = '', status }) {
  const statusColors = { ok: 'var(--green)', warn: 'var(--amber)', bad: 'var(--red)' }
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ color: status ? statusColors[status] : 'var(--text)' }}>
        {value}{unit}
      </div>
    </div>
  )
}

function MiniStat({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 500 }}>{value ?? '—'}</div>
    </div>
  )
}

function EmptyState({ text }) {
  return (
    <div style={{
      background: 'var(--bg)',
      borderRadius: 'var(--radius)',
      padding: '2rem',
      textAlign: 'center',
      color: 'var(--text-3)',
      marginBottom: '1.5rem',
    }}>{text}</div>
  )
}

function NoAccountScreen() {
  const [form, setForm] = useState({ name: '', yandex_login: '', oauth_token: '', target_cpl: '', metrika_counter_id: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      await api.createAccount({
        ...form,
        target_cpl: form.target_cpl ? Number(form.target_cpl) : null,
      })
      window.location.reload()
    } catch (e) {
      setError(e.message)
      setSaving(false)
    }
  }

  return (
    <div style={{ maxWidth: 500, margin: '4rem auto' }}>
      <h1 style={{ fontSize: 20, fontWeight: 500, marginBottom: '0.5rem' }}>Добавить кабинет</h1>
      <p style={{ color: 'var(--text-2)', marginBottom: '1.5rem', fontSize: 13 }}>
        Для начала работы подключите Яндекс Директ через OAuth-токен.
        Инструкция: <a href="https://yandex.ru/dev/direct/doc/dg/concepts/auth-token.html"
          target="_blank" style={{ color: 'var(--blue)' }}>получить токен</a>
      </p>
      <div className="card">
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Field label="Название кабинета" value={form.name}
            onChange={v => setForm(f => ({ ...f, name: v }))} required />
          <Field label="Логин Яндекс" value={form.yandex_login}
            onChange={v => setForm(f => ({ ...f, yandex_login: v }))} required />
          <Field label="OAuth-токен" value={form.oauth_token} type="password"
            onChange={v => setForm(f => ({ ...f, oauth_token: v }))} required />
          <Field label="ID счётчика Метрики" value={form.metrika_counter_id}
            onChange={v => setForm(f => ({ ...f, metrika_counter_id: v }))} />
          <Field label="Целевой CPL, ₽" value={form.target_cpl} type="number"
            onChange={v => setForm(f => ({ ...f, target_cpl: v }))} />
          {error && <div style={{ color: 'var(--red)', fontSize: 12 }}>{error}</div>}
          <button className="btn btn-primary" type="submit" disabled={saving}>
            {saving ? 'Сохранение...' : 'Подключить кабинет'}
          </button>
        </form>
      </div>
    </div>
  )
}

function Field({ label, value, onChange, type = 'text', required }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, color: 'var(--text-2)' }}>{label}{required && ' *'}</span>
      <input type={type} value={value} onChange={e => onChange(e.target.value)}
        required={required} style={{ width: '100%' }} />
    </label>
  )
}
