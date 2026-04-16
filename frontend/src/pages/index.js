import { useState, useEffect, useCallback } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const PERIODS = [
  { label: 'Вчера', days: 1 },
  { label: '3 дня', days: 3 },
  { label: 'Неделя', days: 7 },
  { label: 'Месяц', days: 30 },
]

function fmt(n, suffix = '') {
  if (n == null) return '—'
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M' + suffix
  if (n >= 1000) return Math.round(n).toLocaleString('ru') + suffix
  return (Math.round(n * 10) / 10) + suffix
}
function rub(n) { return n == null ? '—' : Math.round(n).toLocaleString('ru') + ' ₽' }
function pct(n) { return n == null ? '—' : (Math.round(n * 10) / 10) + '%' }
function pos(n) { return (!n || n === 0) ? '—' : (Math.round(n * 10) / 10) }

function calcDelta(curr, prev, invertGood = false) {
  if (!prev || prev === 0) return null
  const d = ((curr - prev) / Math.abs(prev) * 100)
  const isUp = d > 0
  const isGood = invertGood ? !isUp : isUp
  return { val: Math.abs(d).toFixed(1), isUp, cls: isGood ? 'up' : 'down' }
}

function calcQuality(bounce, duration, depth) {
  if (!bounce && !duration) return null
  const b = (1 - (bounce || 0) / 100) * 0.4
  const t = Math.min((duration || 0) / 180, 1) * 0.3
  const d = Math.min((depth || 0) / 3, 1) * 0.2
  return Math.round((b + t + d) * 100 / 0.9)
}

function KPICard({ label, value, delta, prev }) {
  return (
    <div className="kpi-card">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {delta ? (
        <div className={`kpi-delta ${delta.cls}`}>
          {delta.isUp ? '▲' : '▼'} {delta.val}%
          {prev && <span className="kpi-prev">vs {prev}</span>}
        </div>
      ) : (
        <div className="kpi-delta neutral">—</div>
      )}
    </div>
  )
}

function SignalCard({ item, type }) {
  const [open, setOpen] = useState(false)
  const sev = item.severity || (type === 'opportunity' ? 'success' : 'warning')
  return (
    <div
      className={`signal-item ${sev}${open ? ' open' : ''}`}
      onClick={() => setOpen(o => !o)}
    >
      <div className="signal-header">
        <span className={`signal-badge ${sev}`}>
          {sev === 'critical' ? '🔴 Критично' : sev === 'warning' ? '🟡 Важно' : '🟢 Рост'}
        </span>
        {item.priority && (
          <span style={{ fontSize: 10, color: 'var(--text3)' }}>
            {item.priority === 'today' ? '🔴 Сегодня' : item.priority === 'this_week' ? '🟡 Неделя' : '🔵 Месяц'}
          </span>
        )}
      </div>
      <div className="signal-phrase">{item.phrase}</div>
      <div className="signal-desc">{item.description}</div>
      <div className="signal-action">→ {item.action}</div>
      {open && (
        <div className="signal-expanded">
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 11, color: 'var(--text3)' }}>
            {item.clicks != null && <span>Кликов: <b style={{ color: 'var(--text1)' }}>{item.clicks}</b></span>}
            {item.impressions != null && <span>Показов: <b style={{ color: 'var(--text1)' }}>{item.impressions}</b></span>}
            {item.avg_position != null && <span>Позиция: <b style={{ color: 'var(--text1)' }}>{item.avg_position}</b></span>}
            {item.metric_value != null && item.type !== 'traffic_drop' && <span>Метрика: <b style={{ color: 'var(--text1)' }}>{item.metric_value}</b></span>}
            {item.spend > 0 && <span>Расход: <b style={{ color: 'var(--text1)' }}>{rub(item.spend)}</b></span>}
            {item.recommended_bid && <span>Рек. ставка: <b style={{ color: 'var(--accent)' }}>{rub(item.recommended_bid)}</b></span>}
          </div>
          <button
            className="btn btn-sm btn-primary"
            style={{ marginTop: 8 }}
            onClick={e => { e.stopPropagation(); }}
          >
            Взять в работу →
          </button>
        </div>
      )}
    </div>
  )
}

export default function Dashboard() {
  const { account, accounts, accountId, switchAccount, loading } = useAccount()
  const [dash, setDash] = useState(null)
  const [period, setPeriod] = useState('Неделя')
  const [loadingDash, setLoadingDash] = useState(false)

  const loadDash = useCallback(() => {
    if (!accountId) return
    setLoadingDash(true)
    api.getDashboard(accountId)
      .then(setDash)
      .catch(console.error)
      .finally(() => setLoadingDash(false))
  }, [accountId])

  useEffect(() => { loadDash() }, [loadDash])

  if (loading) return (
    <Layout account={null} accounts={[]} onAccountChange={() => {}}>
      <div style={{ padding: 40, color: 'var(--text3)' }}>Загрузка...</div>
    </Layout>
  )

  if (!account && (!accounts || accounts.length === 0)) {
    return (
      <Layout account={null} accounts={[]} onAccountChange={switchAccount}>
        <div style={{ maxWidth: 440, margin: '4rem auto' }}>
          <div className="card" style={{ textAlign: 'center', padding: '2.5rem' }}>
            <div style={{ fontSize: 36, marginBottom: 14 }}>🚀</div>
            <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 8 }}>Добавьте рекламный кабинет</div>
            <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 18 }}>
              Перейдите в Настройки и подключите кабинет Яндекс Директ
            </p>
            <a href="/settings" className="btn btn-primary">Настройки →</a>
          </div>
        </div>
      </Layout>
    )
  }

  const summary = dash?.last_analysis?.summary || {}
  const problems = dash?.last_analysis?.problems || []
  const opps = dash?.last_analysis?.opportunities || []
  const hasCRM = summary.has_crm_data
  const hasMetrika = summary.has_metrika

  const qualityScore = calcQuality(
    summary.metrika_bounce_rate,
    summary.metrika_avg_duration,
    summary.metrika_page_depth
  )
  const qualityColor = !qualityScore ? 'var(--text3)'
    : qualityScore >= 70 ? 'var(--green)'
    : qualityScore >= 50 ? 'var(--yellow)'
    : 'var(--red)'

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      {/* Header */}
      <div className="page-header">
        <div className="page-title">Main Board</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <div className="period-tabs">
            {PERIODS.map(p => (
              <div
                key={p.label}
                className={`period-tab${period === p.label ? ' active' : ''}`}
                onClick={() => setPeriod(p.label)}
              >
                {p.label}
              </div>
            ))}
            <div className={`period-tab${period === 'Свой' ? ' active' : ''}`} onClick={() => setPeriod('Свой')}>
              Свой
            </div>
          </div>
          <button className="btn btn-sm" onClick={loadDash}>↻</button>
        </div>
      </div>

      {loadingDash && !summary.total_clicks && (
        <div style={{ color: 'var(--text3)', fontSize: 13, marginBottom: 16 }}>Загрузка данных...</div>
      )}

      {/* Блок 1: Рекламные */}
      <div className="kpi-section">
        <div className="kpi-section-label">◈ Рекламные показатели</div>
        <div className="kpi-grid">
          <KPICard label="Показы" value={fmt(summary.total_impressions)} />
          <KPICard label="Клики" value={fmt(summary.total_clicks)} />
          <KPICard label="CTR" value={pct(summary.ctr)} />
          <KPICard label="Расход" value={rub(summary.total_spend)} />
          <KPICard label="CPC" value={rub(summary.avg_cpc)} />
          <KPICard label="Ср. позиция" value={pos(summary.avg_position)} />
          <KPICard label="Ключей" value={fmt(summary.keywords_analyzed)} />
          <KPICard
            label="Проблем"
            value={<span style={{ color: problems.length > 0 ? 'var(--red)' : 'var(--green)' }}>{problems.length}</span>}
          />
        </div>
      </div>

      {/* Блок 2: CRM */}
      <div className="kpi-section">
        <div className="kpi-section-label">◎ Результат (CRM)</div>
        {hasCRM ? (
          <div className="kpi-grid">
            <KPICard label="MQL" value={fmt(summary.total_leads)} />
            <KPICard label="CPL" value={rub(summary.cpl)} />
            <KPICard label="CR клик→MQL" value={summary.cr_click_lead ? pct(summary.cr_click_lead * 100) : '—'} />
            <KPICard label="SQL" value={fmt(summary.total_sqls)} />
            <KPICard label="CPsql" value={rub(summary.cpql)} />
          </div>
        ) : (
          <div className="crm-placeholder">
            Данные CRM не подключены — <a href="/settings">подключить выгрузку из 1С</a>
          </div>
        )}
      </div>

      {/* Блок 3: Поведение */}
      <div className="kpi-section">
        <div className="kpi-section-label">◑ Поведение (Метрика)</div>
        {hasMetrika ? (
          <div className="kpi-grid">
            <KPICard label="Визиты" value={fmt(summary.metrika_visits)} />
            <KPICard label="Отказы" value={pct(summary.metrika_bounce_rate)} />
            <KPICard label="Глубина" value={summary.metrika_page_depth ? (Math.round(summary.metrika_page_depth * 10) / 10) : '—'} />
            <KPICard label="Время на сайте" value={summary.metrika_avg_duration ? Math.round(summary.metrika_avg_duration) + 'с' : '—'} />
            {qualityScore != null && (
              <div className="kpi-card" style={{ gridColumn: 'span 2' }}>
                <div className="kpi-label">Качество трафика</div>
                <div className="kpi-value" style={{ color: qualityColor }}>{qualityScore}%</div>
                <div className="quality-bar">
                  <div className="quality-fill" style={{ width: qualityScore + '%', background: qualityColor }} />
                </div>
                <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 4 }}>
                  {qualityScore >= 70 ? '🟢 Хороший' : qualityScore >= 50 ? '🟡 Средний' : '🔴 Плохой'} трафик
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="crm-placeholder">Метрика не подключена или нет данных</div>
        )}
      </div>

      {/* Сигналы */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <div className="card">
          <div className="card-title">
            Проблемы
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>{problems.length} найдено</span>
          </div>
          {problems.length === 0 ? (
            <div className="empty-state" style={{ padding: '1.5rem 0' }}>
              <div className="empty-icon">✓</div>
              <div className="empty-title">Проблем не обнаружено</div>
            </div>
          ) : (
            problems.slice(0, 6).map((p, i) => <SignalCard key={i} item={p} />)
          )}
          {problems.length > 6 && (
            <a href="/suggestions" style={{ fontSize: 12, color: 'var(--accent)', display: 'block', marginTop: 8 }}>
              Ещё {problems.length - 6} проблем →
            </a>
          )}
        </div>

        <div className="card">
          <div className="card-title">
            Точки роста
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>{opps.length} найдено</span>
          </div>
          {opps.length === 0 ? (
            <div className="empty-state" style={{ padding: '1.5rem 0' }}>
              <div className="empty-icon">◈</div>
              <div className="empty-title">Накапливается статистика</div>
              <div className="empty-desc">Точки роста появятся после сбора данных</div>
            </div>
          ) : (
            opps.map((o, i) => <SignalCard key={i} item={o} type="opportunity" />)
          )}
        </div>
      </div>
    </Layout>
  )
}
