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

/* ── formatters ── */
function fNum(n)        { if (n==null) return '—'; if (n>=1e6) return (n/1e6).toFixed(1)+'M'; if (n>=1000) return Math.round(n).toLocaleString('ru'); return Math.round(n*10)/10 }
function fRub(n)        { return n==null ? '—' : Math.round(n).toLocaleString('ru')+' ₽' }
function fPct(n)        { return n==null ? '—' : (Math.round(n*10)/10)+'%' }
function fPos(n)        { return (!n||n===0) ? '—' : (Math.round(n*10)/10) }
function fSec(n)        { if (!n) return '—'; if (n>=60) return Math.floor(n/60)+'м '+(Math.round(n)%60)+'с'; return Math.round(n)+'с' }

function DeltaBadge({ delta, invert }) {
  if (!delta) return <span className="kpi-delta neutral">—</span>
  const up = delta.value > 0
  const good = invert ? !up : up
  return (
    <div className={`kpi-delta ${good?'up':'down'}`}>
      {up ? '▲' : '▼'} {Math.abs(delta.value)}%
    </div>
  )
}

function KPICard({ label, value, delta, prev, invert, alert }) {
  return (
    <div className="kpi-card">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={alert?{color:'var(--red)'}:{}}>{value}</div>
      <DeltaBadge delta={delta} invert={invert} />
      {prev != null && prev !== 0 && (
        <div style={{fontSize:10,color:'var(--text3)',marginTop:2}}>
          пред: {typeof prev==='number' && prev>1000 ? prev.toLocaleString('ru') : prev}
        </div>
      )}
    </div>
  )
}

function SignalItem({ item, type, onTakeAction }) {
  const [open, setOpen] = useState(false)
  const sev = item.severity||(type==='opp'?'success':'warning')
  const sevLabel = sev==='critical'?'🔴 Критично':sev==='warning'?'🟡 Важно':'🟢 Рост'
  const priLabel = {today:'Сегодня',this_week:'Неделя',month:'Месяц'}[item.priority]||''
  return (
    <div className={`signal-item ${sev}${open?' open':''}`} onClick={()=>setOpen(o=>!o)}>
      <div className="signal-header">
        <span className={`signal-badge ${sev}`}>{sevLabel}</span>
        {priLabel && <span style={{fontSize:10,color:'var(--text3)'}}>🕐 {priLabel}</span>}
      </div>
      <div className="signal-phrase">{item.phrase}</div>
      <div className="signal-desc">{item.description}</div>
      <div className="signal-action">→ {item.action}</div>
      {open && (
        <div className="signal-expanded" onClick={e=>e.stopPropagation()}>
          <div style={{display:'flex',gap:12,flexWrap:'wrap',fontSize:11,color:'var(--text3)',marginBottom:8}}>
            {item.clicks!=null && <span>Кликов: <b style={{color:'var(--text1)'}}>{item.clicks}</b></span>}
            {item.avg_position!=null && <span>Поз: <b style={{color:'var(--text1)'}}>{item.avg_position}</b></span>}
            {item.spend>0 && <span>Расход: <b style={{color:'var(--text1)'}}>{fRub(item.spend)}</b></span>}
            {item.recommended_bid && <span>Рек.ставка: <b style={{color:'var(--accent)'}}>{fRub(item.recommended_bid)}</b></span>}
          </div>
          <button className="btn btn-sm btn-primary"
            onClick={()=>onTakeAction&&onTakeAction(item)}>
            ✓ Взять в работу
          </button>
        </div>
      )}
    </div>
  )
}

function TopCampaigns({ campaigns }) {
  if (!campaigns?.length) return null
  return (
    <div className="card" style={{marginTop:14}}>
      <div className="card-title">Топ кампании по расходу</div>
      <table>
        <thead>
          <tr>
            <th>Кампания</th>
            <th>Расход</th>
            <th>Клики</th>
            <th>Позиция</th>
            <th>Стратегия</th>
          </tr>
        </thead>
        <tbody>
          {campaigns.map(c => (
            <tr key={c.id}>
              <td style={{maxWidth:260,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',fontWeight:500,fontSize:12}}>{c.name}</td>
              <td>{fRub(c.spend)}</td>
              <td>{fNum(c.clicks)}</td>
              <td>{fPos(c.avg_position)}</td>
              <td>
                <span className={`badge ${c.strategy_type==='MANUAL_CPC'?'badge-ok':'badge-info'}`}>
                  {c.strategy_type==='MANUAL_CPC'?'Ручная':'Авто'}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Dashboard() {
  const { account, accounts, accountId, switchAccount, loading } = useAccount()
  const [period, setPeriod] = useState('week')
  const [dash, setDash] = useState(null)
  const [loadingDash, setLoadingDash] = useState(false)
  const [hypothesesCreated, setHypothesesCreated] = useState([])

  const load = useCallback(() => {
    if (!accountId) return
    setLoadingDash(true)
    api.getDashboard(accountId, period)
      .then(setDash)
      .catch(console.error)
      .finally(()=>setLoadingDash(false))
  }, [accountId, period])

  useEffect(()=>{ load() }, [load])

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
      setHypothesesCreated(prev => [...prev, item.phrase])
      alert(`✓ Гипотеза создана для: ${item.phrase}`)
    } catch(e) {
      console.error(e)
      alert('Ошибка при создании гипотезы')
    }
  }

  if (loading) return (
    <Layout account={null} accounts={[]} onAccountChange={()=>{}}>
      <div style={{padding:40,color:'var(--text3)'}}>Загрузка...</div>
    </Layout>
  )

  if (!account && !accounts?.length) return (
    <Layout account={null} accounts={[]} onAccountChange={switchAccount}>
      <div style={{maxWidth:440,margin:'4rem auto'}}>
        <div className="card" style={{textAlign:'center',padding:'2.5rem'}}>
          <div style={{fontSize:36,marginBottom:14}}>🚀</div>
          <div style={{fontWeight:600,fontSize:16,marginBottom:8}}>Добавьте рекламный кабинет</div>
          <p style={{color:'var(--text2)',fontSize:13,marginBottom:18}}>Перейдите в Настройки и подключите кабинет Яндекс Директ</p>
          <a href="/settings" className="btn btn-primary">Настройки →</a>
        </div>
      </div>
    </Layout>
  )

  const ad = dash?.ad_kpi || {}
  const beh = dash?.behavior || {}
  const problems = dash?.problems || []
  const opps = dash?.opportunities || []
  const urgentCount = problems.filter(p=>p.priority==='today').length
  const qs = beh.quality_score
  const qc = !qs?'var(--text3)':qs>=70?'var(--green)':qs>=50?'var(--yellow)':'var(--red)'

  const pd = dash?.period_dates
  const periodLabel = pd ? `${pd.curr_start} — ${pd.curr_end}` : ''
  const prevLabel   = pd ? `${pd.prev_start} — ${pd.prev_end}` : ''

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      {/* Header */}
      <div className="page-header">
        <div>
          <div className="page-title">Main Board</div>
          {periodLabel && (
            <div style={{fontSize:11,color:'var(--text3)',marginTop:2}}>
              {periodLabel} <span style={{opacity:.6}}>vs {prevLabel}</span>
            </div>
          )}
        </div>
        <div style={{display:'flex',gap:8,alignItems:'center'}}>
          <div className="period-tabs">
            {PERIODS.map(p=>(
              <div key={p.key} className={`period-tab${period===p.key?' active':''}`}
                onClick={()=>setPeriod(p.key)}>{p.label}</div>
            ))}
          </div>
          <button className="btn btn-sm" onClick={load} title="Обновить" disabled={loadingDash}>
            {loadingDash ? '⏳' : '↻'}
          </button>
        </div>
      </div>

      {/* Urgent banner */}
      {urgentCount > 0 && (
        <div style={{
          background:'rgba(255,79,79,0.08)',border:'1px solid rgba(255,79,79,0.2)',
          borderRadius:'var(--radius)',padding:'10px 14px',marginBottom:14,
          display:'flex',justifyContent:'space-between',alignItems:'center',
        }}>
          <span style={{fontSize:13}}>🔴 <strong>{urgentCount}</strong> предложений требуют внимания сегодня</span>
          <a href="/suggestions" className="btn btn-sm" style={{color:'var(--red)',borderColor:'rgba(255,79,79,0.3)'}}>Смотреть →</a>
        </div>
      )}

      {/* Блок 1: Рекламные */}
      <div className="kpi-section">
        <div className="kpi-section-label">◈ Рекламные показатели</div>
        <div className="kpi-grid">
          <KPICard label="Показы"          value={fNum(ad.impressions?.value)}        delta={ad.impressions?.delta}        prev={ad.impressions?.prev} />
          <KPICard label="Клики"           value={fNum(ad.clicks?.value)}             delta={ad.clicks?.delta}             prev={ad.clicks?.prev} />
          <KPICard label="CTR"             value={fPct(ad.ctr?.value)}                delta={ad.ctr?.delta}                prev={ad.ctr?.prev ? fPct(ad.ctr.prev) : null} />
          <KPICard label="Расход"          value={fRub(ad.spend?.value)}              delta={ad.spend?.delta}              prev={ad.spend?.prev ? fRub(ad.spend.prev) : null} invert />
          <KPICard label="CPC"             value={fRub(ad.avg_cpc?.value)}            delta={ad.avg_cpc?.delta}            prev={ad.avg_cpc?.prev ? fRub(ad.avg_cpc.prev) : null} invert />
          <KPICard label="Ср. объём трафика" value={fNum(ad.avg_traffic_volume?.value)} delta={ad.avg_traffic_volume?.delta} prev={ad.avg_traffic_volume?.prev} />
          <KPICard label="Ср. поз. показа" value={fPos(ad.avg_position?.value)}       delta={ad.avg_position?.delta}       prev={ad.avg_position?.prev ? fPos(ad.avg_position.prev) : null} invert />
          <KPICard label="Ср. поз. клика"  value={fPos(ad.avg_click_position?.value)} delta={ad.avg_click_position?.delta} prev={ad.avg_click_position?.prev ? fPos(ad.avg_click_position.prev) : null} invert />
          <KPICard label="Активных РК"     value={fNum(dash?.active_campaigns)} />
        </div>
      </div>

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
            <KPICard label="Глубина"     value={beh.page_depth ? (Math.round(beh.page_depth*10)/10) : '—'} />
            <KPICard label="Время"       value={fSec(beh.avg_duration)} />
            {qs != null && (
              <div className="kpi-card" style={{gridColumn:'span 2'}}>
                <div className="kpi-label">Качество трафика</div>
                <div className="kpi-value" style={{color:qc}}>{qs}%</div>
                <div className="quality-bar">
                  <div className="quality-fill" style={{width:qs+'%',background:qc}} />
                </div>
                <div style={{fontSize:10,color:'var(--text3)',marginTop:4}}>
                  {qs>=70?'🟢 Хороший':qs>=50?'🟡 Средний':'🔴 Плохой'} трафик
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="crm-placeholder">Нет данных из Метрики за этот период</div>
        )}
      </div>

      {/* Сигналы */}
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}>
        <div className="card">
          <div className="card-title">
            Проблемы
            <span style={{fontSize:11,color:'var(--text3)'}}>{problems.length} найдено</span>
          </div>
          {problems.length===0 ? (
            <div className="empty-state" style={{padding:'1.5rem 0'}}>
              <div className="empty-icon">✓</div>
              <div className="empty-title">Проблем не обнаружено</div>
              <div className="empty-desc">по данным за выбранный период</div>
            </div>
          ) : problems.slice(0,6).map((p,i)=>(
            <SignalItem key={i} item={p} onTakeAction={handleTakeAction} />
          ))}
          {problems.length>6 && (
            <a href="/suggestions" style={{fontSize:12,color:'var(--accent)',display:'block',marginTop:8}}>
              Ещё {problems.length-6} →
            </a>
          )}
        </div>
        <div className="card">
          <div className="card-title">
            Точки роста
            <span style={{fontSize:11,color:'var(--text3)'}}>{opps.length} найдено</span>
          </div>
          {opps.length===0 ? (
            <div className="empty-state" style={{padding:'1.5rem 0'}}>
              <div className="empty-icon">◈</div>
              <div className="empty-title">Накапливается статистика</div>
              <div className="empty-desc">Точки роста появятся после нескольких дней сбора</div>
            </div>
          ) : opps.map((o,i)=>(
            <SignalItem key={i} item={o} type="opp" onTakeAction={handleTakeAction} />
          ))}
        </div>
      </div>

      {/* Топ кампании */}
      <TopCampaigns campaigns={dash?.top_campaigns} />
    </Layout>
  )
}
