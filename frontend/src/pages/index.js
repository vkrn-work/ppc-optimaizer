import { useState, useEffect, useCallback } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const PERIODS = [
  { key: 'yesterday', label: 'Вчера' },
  { key: '3d',        label: '3 дня' },
  { key: 'week',      label: 'Неделя' },
  { key: 'month',     label: 'Месяц' },
]

function fNum(n)  { if (n==null) return '—'; if (n>=1e6) return (n/1e6).toFixed(1)+'M'; if (n>=1000) return Math.round(n).toLocaleString('ru'); return Math.round(n*10)/10 }
function fRub(n)  { return n==null ? '—' : Math.round(n).toLocaleString('ru')+' ₽' }
function fPct(n)  { return n==null ? '—' : (Math.round(n*10)/10)+'%' }
function fPos(n)  { return (!n||n===0) ? '—' : (Math.round(n*10)/10) }
function fSec(n)  { if (!n) return '—'; if (n>=60) return Math.floor(n/60)+'м '+(Math.round(n)%60)+'с'; return Math.round(n)+'с' }

// Мини-спарклайн из SVG
function Sparkline({ data, field, color = '#5b7bff', height = 28 }) {
  if (!data?.length || data.length < 2) return null
  const vals = data.map(d => d[field] || 0)
  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const range = max - min || 1
  const w = 60, h = height
  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * w
    const y = h - ((v - min) / range) * (h - 4) - 2
    return `${x},${y}`
  }).join(' ')
  // Тренд последних 3 точек
  const last3 = vals.slice(-3)
  const trend = last3[last3.length-1] - last3[0]
  const tColor = trend > 0 ? 'var(--green)' : trend < 0 ? 'var(--red)' : 'var(--text3)'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <svg width={w} height={h} style={{ overflow: 'visible' }}>
        <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.8" />
        {/* последняя точка */}
        {(() => {
          const lastPt = pts.split(' ').pop()
          const [lx, ly] = lastPt.split(',')
          return <circle cx={lx} cy={ly} r="2.5" fill={tColor} />
        })()}
      </svg>
      <span style={{ fontSize: 9, color: tColor, fontWeight: 600 }}>
        {trend > 0 ? '↑' : trend < 0 ? '↓' : '→'}
      </span>
    </div>
  )
}

function DeltaBadge({ delta, invert, prev, prevLabel }) {
  if (!delta) return <div className="kpi-delta neutral">—</div>
  const up = delta.value > 0
  const good = invert ? !up : up
  return (
    <div>
      <div className={`kpi-delta ${good ? 'up' : 'down'}`}>
        {up ? '▲' : '▼'} {Math.abs(delta.value)}%
      </div>
      {prev != null && prev !== 0 && (
        <div style={{ fontSize: 9, color: 'var(--text3)', marginTop: 1 }}>
          {prevLabel || 'пред'}: {typeof prev === 'number' && prev > 1000 ? prev.toLocaleString('ru') : prev}
        </div>
      )}
    </div>
  )
}

function KPICard({ label, value, delta, prev, invert, dailyData, sparkField, prevLabel }) {
  return (
    <div className="kpi-card">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginTop: 4 }}>
        <DeltaBadge delta={delta} invert={invert} prev={prev} prevLabel={prevLabel} />
        {dailyData && sparkField && (
          <Sparkline data={dailyData} field={sparkField} />
        )}
      </div>
    </div>
  )
}

function SignalItem({ item, type, onTakeAction }) {
  const [open, setOpen] = useState(false)
  const sev = item.severity || (type === 'opp' ? 'success' : 'warning')
  return (
    <div className={`signal-item ${sev}${open ? ' open' : ''}`} onClick={() => setOpen(o => !o)}>
      <div className="signal-header">
        <span className={`signal-badge ${sev}`}>
          {sev === 'critical' ? '🔴 Критично' : sev === 'warning' ? '🟡 Важно' : '🟢 Рост'}
        </span>
        {item.priority && (
          <span style={{ fontSize: 10, color: 'var(--text3)' }}>
            {{ today: '🔴 Сегодня', this_week: '🟡 Неделя', month: '🔵 Месяц' }[item.priority] || ''}
          </span>
        )}
      </div>
      <div className="signal-phrase">{item.phrase}</div>
      <div className="signal-desc">{item.description}</div>
      <div className="signal-action">→ {item.action}</div>
      {open && (
        <div className="signal-expanded" onClick={e => e.stopPropagation()}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 11, color: 'var(--text3)', marginBottom: 8 }}>
            {item.clicks != null && <span>Кликов: <b style={{ color: 'var(--text1)' }}>{item.clicks}</b></span>}
            {item.avg_position != null && <span>Поз: <b style={{ color: 'var(--text1)' }}>{item.avg_position}</b></span>}
            {item.spend > 0 && <span>Расход: <b style={{ color: 'var(--text1)' }}>{fRub(item.spend)}</b></span>}
            {item.recommended_bid && <span>Рек.ставка: <b style={{ color: 'var(--accent)' }}>{fRub(item.recommended_bid)}</b></span>}
          </div>
          <button className="btn btn-sm btn-primary" onClick={() => onTakeAction && onTakeAction(item)}>
            ✓ Взять в работу
          </button>
        </div>
      )}
    </div>
  )
}

function MiniChart({ data }) {
  if (!data?.length) return null
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="card-title">
        Динамика по дням
        <span style={{ fontSize: 11, color: 'var(--text3)' }}>{data.length} дней</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th>Дата</th>
              <th>Клики</th>
              <th>Показы</th>
              <th>Расход</th>
              <th>Позиция</th>
              <th>CTR</th>
            </tr>
          </thead>
          <tbody>
            {[...data].reverse().map((d, i) => (
              <tr key={i}>
                <td style={{ fontSize: 12, color: 'var(--text2)', whiteSpace: 'nowrap' }}>
                  {new Date(d.date).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })}
                </td>
                <td style={{ fontWeight: i === 0 ? 600 : 400 }}>{fNum(d.clicks)}</td>
                <td>{fNum(d.impressions)}</td>
                <td>{fRub(d.spend)}</td>
                <td>
                  <span style={{
                    color: d.avg_position > 3 ? 'var(--red)' : d.avg_position < 2 ? 'var(--green)' : 'inherit'
                  }}>
                    {fPos(d.avg_position)}
                  </span>
                </td>
                <td>{fPct(d.ctr)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const { account, accounts, accountId, switchAccount, loading } = useAccount()
  const [period, setPeriod] = useState('week')
  const [dash, setDash] = useState(null)
  const [loadingDash, setLoadingDash] = useState(false)
  const [showDaily, setShowDaily] = useState(false)

  const load = useCallback(() => {
    if (!accountId) return
    setLoadingDash(true)
    api.getDashboard(accountId, period)
      .then(setDash)
      .catch(console.error)
      .finally(() => setLoadingDash(false))
  }, [accountId, period])

  useEffect(() => { load() }, [load])

  async function handleTakeAction(item) {
    if (!accountId) return
    try {
      await api.createHypothesis(accountId, {
        source: 'suggestion',
        phrase: item.phrase,
        change_description: item.action,
        forecast: item.description,
        problem_type: item.type,
        keyword_id: item.keyword_id,
      })
      alert(`✓ Гипотеза создана для: ${item.phrase}`)
    } catch (e) {
      alert('Ошибка: ' + e.message)
    }
  }

  if (loading) return (
    <Layout account={null} accounts={[]} onAccountChange={() => {}}>
      <div style={{ padding: 40, color: 'var(--text3)' }}>Загрузка...</div>
    </Layout>
  )

  if (!account && !accounts?.length) return (
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

  const ad = dash?.ad_kpi || {}
  const beh = dash?.behavior || {}
  const problems = dash?.problems || []
  const opps = dash?.opportunities || []
  const daily = dash?.daily_stats || []
  const urgentCount = problems.filter(p => p.priority === 'today').length
  const qs = beh.quality_score
  const qc = !qs ? 'var(--text3)' : qs >= 70 ? 'var(--green)' : qs >= 50 ? 'var(--yellow)' : 'var(--red)'

  const pd = dash?.period_dates
  const periodLabel = dash?.period_label || ''
  const prevLabel = period === 'yesterday' ? 'ср.14д' : 'пред'

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      {/* Header */}
      <div className="page-header">
        <div>
          <div className="page-title">Main Board</div>
          {periodLabel && (
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
              {periodLabel}
              {pd && <span style={{ opacity: 0.5, marginLeft: 6 }}>{pd.curr_start} — {pd.curr_end}</span>}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <div className="period-tabs">
            {PERIODS.map(p => (
              <div key={p.key} className={`period-tab${period === p.key ? ' active' : ''}`}
                onClick={() => setPeriod(p.key)}>{p.label}</div>
            ))}
          </div>
          <button
            className={`btn btn-sm${showDaily ? ' btn-primary' : ''}`}
            onClick={() => setShowDaily(s => !s)}
            title="По дням"
          >
            ≡ По дням
          </button>
          <button className="btn btn-sm" onClick={load} disabled={loadingDash}>
            {loadingDash ? '⏳' : '↻'}
          </button>
        </div>
      </div>

      {/* Urgent banner */}
      {urgentCount > 0 && (
        <div style={{
          background: 'rgba(255,79,79,0.08)', border: '1px solid rgba(255,79,79,0.2)',
          borderRadius: 'var(--radius)', padding: '10px 14px', marginBottom: 14,
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span style={{ fontSize: 13 }}>🔴 <strong>{urgentCount}</strong> предложений требуют внимания сегодня</span>
          <a href="/suggestions" className="btn btn-sm" style={{ color: 'var(--red)', borderColor: 'rgba(255,79,79,0.3)' }}>Смотреть →</a>
        </div>
      )}

      {/* Блок 1: Рекламные */}
      <div className="kpi-section">
        <div className="kpi-section-label">◈ Рекламные показатели</div>
        <div className="kpi-grid">
          <KPICard label="Показы"           value={fNum(ad.impressions?.value)}        delta={ad.impressions?.delta}        prev={ad.impressions?.prev}        dailyData={daily} sparkField="impressions" prevLabel={prevLabel} />
          <KPICard label="Клики"            value={fNum(ad.clicks?.value)}             delta={ad.clicks?.delta}             prev={ad.clicks?.prev}             dailyData={daily} sparkField="clicks"      prevLabel={prevLabel} />
          <KPICard label="CTR"              value={fPct(ad.ctr?.value)}                delta={ad.ctr?.delta}                prev={ad.ctr?.prev ? fPct(ad.ctr.prev) : null}               dailyData={daily} sparkField="ctr" prevLabel={prevLabel} />
          <KPICard label="Расход"           value={fRub(ad.spend?.value)}              delta={ad.spend?.delta}              prev={ad.spend?.prev ? fRub(ad.spend.prev) : null}           dailyData={daily} sparkField="spend"      invert prevLabel={prevLabel} />
          <KPICard label="CPC"              value={fRub(ad.avg_cpc?.value)}            delta={ad.avg_cpc?.delta}            prev={ad.avg_cpc?.prev ? fRub(ad.avg_cpc.prev) : null}       invert prevLabel={prevLabel} />
          <KPICard label="Ср. объём тр."   value={fNum(ad.avg_traffic_volume?.value)} delta={ad.avg_traffic_volume?.delta} prev={ad.avg_traffic_volume?.prev}  prevLabel={prevLabel} />
          <KPICard label="Поз. показа"      value={fPos(ad.avg_position?.value)}       delta={ad.avg_position?.delta}       prev={ad.avg_position?.prev ? fPos(ad.avg_position.prev) : null}       dailyData={daily} sparkField="avg_position" invert prevLabel={prevLabel} />
          <KPICard label="Поз. клика"       value={fPos(ad.avg_click_position?.value)} delta={ad.avg_click_position?.delta} prev={ad.avg_click_position?.prev ? fPos(ad.avg_click_position.prev) : null} invert prevLabel={prevLabel} />
          <KPICard label="Активных РК"      value={fNum(dash?.active_campaigns)} />
        </div>
      </div>

      {/* Таблица по дням */}
      {showDaily && daily.length > 0 && <MiniChart data={daily} />}

      {/* Блок 2: CRM */}
      <div className="kpi-section">
        <div className="kpi-section-label">◎ Результат (CRM)</div>
        <div className="crm-placeholder">
          Данные CRM не подключены — <a href="/settings">подключить выгрузку из 1С</a>
        </div>
      </div>

      {/* Блок 3: Поведение */}
      <div className="kpi-section">
        <div className="kpi-section-label">◑ Поведение (Метрика)</div>
        {beh.has_metrika ? (
          <div className="kpi-grid">
            <KPICard label="Визиты"      value={fNum(beh.visits)} />
            <KPICard label="Отказы"      value={fPct(beh.bounce_rate)} invert />
            <KPICard label="Глубина"     value={beh.page_depth ? (Math.round(beh.page_depth * 10) / 10) : '—'} />
            <KPICard label="Время"       value={fSec(beh.avg_duration)} />
            {qs != null && (
              <div className="kpi-card" style={{ gridColumn: 'span 2' }}>
                <div className="kpi-label">Качество трафика</div>
                <div className="kpi-value" style={{ color: qc }}>{qs}%</div>
                <div className="quality-bar">
                  <div className="quality-fill" style={{ width: qs + '%', background: qc }} />
                </div>
                <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 4 }}>
                  {qs >= 70 ? '🟢 Хороший' : qs >= 50 ? '🟡 Средний' : '🔴 Плохой'} трафик
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="crm-placeholder">Нет данных из Метрики</div>
        )}
      </div>

      {/* Сигналы */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <div className="card">
          <div className="card-title">
            Проблемы
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>{problems.length}</span>
          </div>
          {problems.length === 0 ? (
            <div className="empty-state" style={{ padding: '1rem 0' }}>
              <div className="empty-icon">✓</div>
              <div className="empty-title">Проблем не обнаружено</div>
            </div>
          ) : problems.slice(0, 6).map((p, i) => (
            <SignalItem key={i} item={p} onTakeAction={handleTakeAction} />
          ))}
          {problems.length > 6 && (
            <a href="/suggestions" style={{ fontSize: 12, color: 'var(--accent)', display: 'block', marginTop: 8 }}>
              Ещё {problems.length - 6} →
            </a>
          )}
        </div>
        <div className="card">
          <div className="card-title">
            Точки роста
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>{opps.length}</span>
          </div>
          {opps.length === 0 ? (
            <div className="empty-state" style={{ padding: '1rem 0' }}>
              <div className="empty-icon">◈</div>
              <div className="empty-title">Накапливается статистика</div>
            </div>
          ) : opps.map((o, i) => (
            <SignalItem key={i} item={o} type="opp" onTakeAction={handleTakeAction} />
          ))}
        </div>
      </div>

      {/* Топ кампании */}
      {dash?.top_campaigns?.length > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="card-title">Топ кампании по расходу</div>
          <table>
            <thead>
              <tr><th>Кампания</th><th>Расход</th><th>Клики</th><th>Позиция</th><th>Стратегия</th></tr>
            </thead>
            <tbody>
              {dash.top_campaigns.map(c => (
                <tr key={c.id}>
                  <td style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 500, fontSize: 12 }}>{c.name}</td>
                  <td>{fRub(c.spend)}</td>
                  <td>{fNum(c.clicks)}</td>
                  <td>{fPos(c.avg_position)}</td>
                  <td>
                    <span className={`badge ${c.strategy_type === 'MANUAL_CPC' ? 'badge-ok' : 'badge-info'}`}>
                      {c.strategy_type === 'MANUAL_CPC' ? 'Ручная' : 'Авто'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Layout>
  )
}
