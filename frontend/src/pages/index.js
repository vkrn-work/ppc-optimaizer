import { useState, useEffect, useCallback } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const PERIODS = ['Вчера', '3 дня', 'Неделя', 'Месяц', 'Свой']

function fmt(n, suffix='') {
  if (n == null || n === '') return '—'
  if (n >= 1000000) return (n/1000000).toFixed(1)+'M'+suffix
  if (n >= 1000) return Math.round(n).toLocaleString('ru')+suffix
  return (Math.round(n*10)/10)+suffix
}
function rub(n) { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }
function pct(n) { return n==null?'—':(Math.round(n*10)/10)+'%' }
function posF(n) { return (!n||n===0)?'—':(Math.round(n*10)/10) }

function calcDelta(curr, prev, invertGood=false) {
  if (!prev || prev===0 || curr==null) return null
  const d = (curr - prev) / Math.abs(prev) * 100
  const isUp = d > 0
  const isGood = invertGood ? !isUp : isUp
  return { val: Math.abs(d).toFixed(1), isUp, cls: isGood?'up':'down' }
}

function calcQuality(bounce, duration, depth) {
  if (!bounce && !duration) return null
  const b = (1-(bounce||0)/100)*0.4
  const t = Math.min((duration||0)/180,1)*0.3
  const d = Math.min((depth||0)/3,1)*0.2
  return Math.round((b+t+d)*100/0.9)
}

function KPICard({ label, value, delta, prev, alert }) {
  const qualityColor = alert ? 'var(--red)' : null
  return (
    <div className="kpi-card">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={qualityColor?{color:qualityColor}:{}}>{value}</div>
      {delta ? (
        <div className={`kpi-delta ${delta.cls}`}>
          {delta.isUp?'▲':'▼'} {delta.val}%
          {prev && <span className="kpi-prev">vs {prev}</span>}
        </div>
      ) : <div className="kpi-delta neutral">—</div>}
    </div>
  )
}

function SignalCard({ item, type }) {
  const [open, setOpen] = useState(false)
  const sev = item.severity||(type==='opportunity'?'success':'warning')
  const sevLabel = sev==='critical'?'🔴 Критично':sev==='warning'?'🟡 Важно':'🟢 Рост'
  const priLabel = {today:'🔴 Сегодня',this_week:'🟡 Неделя',month:'🔵 Месяц'}[item.priority]||''
  return (
    <div className={`signal-item ${sev}${open?' open':''}`} onClick={()=>setOpen(o=>!o)}>
      <div className="signal-header">
        <span className={`signal-badge ${sev}`}>{sevLabel}</span>
        {priLabel && <span style={{fontSize:10,color:'var(--text3)'}}>{priLabel}</span>}
      </div>
      <div className="signal-phrase">{item.phrase}</div>
      <div className="signal-desc">{item.description}</div>
      <div className="signal-action">→ {item.action}</div>
      {open && (
        <div className="signal-expanded">
          <div style={{display:'flex',gap:12,flexWrap:'wrap',fontSize:11,color:'var(--text3)'}}>
            {item.clicks!=null && <span>Кликов: <b style={{color:'var(--text1)'}}>{item.clicks}</b></span>}
            {item.avg_position!=null && <span>Позиция: <b style={{color:'var(--text1)'}}>{item.avg_position}</b></span>}
            {item.spend>0 && <span>Расход: <b style={{color:'var(--text1)'}}>{rub(item.spend)}</b></span>}
            {item.recommended_bid && <span>Рек. ставка: <b style={{color:'var(--accent)'}}>{rub(item.recommended_bid)}</b></span>}
          </div>
          <button className="btn btn-sm btn-primary" style={{marginTop:8}}
            onClick={e=>{e.stopPropagation()}}>Взять в работу →</button>
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
    api.getDashboard(accountId).then(setDash).catch(console.error).finally(()=>setLoadingDash(false))
  }, [accountId])

  useEffect(()=>{ loadDash() }, [loadDash])

  if (loading) return (
    <Layout account={null} accounts={[]} onAccountChange={()=>{}}>
      <div style={{padding:40,color:'var(--text3)'}}>Загрузка...</div>
    </Layout>
  )

  if (!account && (!accounts||accounts.length===0)) return (
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

  const s = dash?.last_analysis?.summary || {}
  const problems = dash?.last_analysis?.problems || []
  const opps = dash?.last_analysis?.opportunities || []
  const qs = calcQuality(s.metrika_bounce_rate, s.metrika_avg_duration, s.metrika_page_depth)
  const qc = !qs?'var(--text3)':qs>=70?'var(--green)':qs>=50?'var(--yellow)':'var(--red)'
  const urgentToday = problems.filter(p=>p.priority==='today').length

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      {/* Header */}
      <div className="page-header">
        <div className="page-title">Main Board</div>
        <div style={{display:'flex',gap:8,alignItems:'center'}}>
          <div className="period-tabs">
            {PERIODS.map(p=>(
              <div key={p} className={`period-tab${period===p?' active':''}`} onClick={()=>setPeriod(p)}>{p}</div>
            ))}
          </div>
          <button className="btn btn-sm" onClick={loadDash} title="Обновить">↻</button>
        </div>
      </div>

      {/* Urgent banner */}
      {urgentToday > 0 && (
        <div style={{
          background:'rgba(255,79,79,0.08)', border:'1px solid rgba(255,79,79,0.2)',
          borderRadius:'var(--radius)', padding:'10px 14px', marginBottom:14,
          display:'flex', justifyContent:'space-between', alignItems:'center',
        }}>
          <span style={{fontSize:13}}>🔴 <strong>{urgentToday}</strong> предложений требуют внимания сегодня</span>
          <a href="/suggestions" className="btn btn-sm" style={{color:'var(--red)',borderColor:'rgba(255,79,79,0.3)'}}>Смотреть →</a>
        </div>
      )}

      {/* Блок 1: Рекламные */}
      <div className="kpi-section">
        <div className="kpi-section-label">◈ Рекламные показатели</div>
        <div className="kpi-grid">
          <KPICard label="Показы" value={fmt(s.total_impressions)} />
          <KPICard label="Клики" value={fmt(s.total_clicks)} />
          <KPICard label="CTR" value={pct(s.ctr)} />
          <KPICard label="Расход" value={rub(s.total_spend)} />
          <KPICard label="CPC" value={rub(s.avg_cpc)} />
          <KPICard label="Ср. объём трафика" value={s.avg_traffic_volume ? fmt(s.avg_traffic_volume) : '—'} />
          <KPICard label="Ср. поз. показа" value={posF(s.avg_position)} />
          <KPICard label="Ср. поз. клика" value={posF(s.avg_click_position)} />
          <KPICard label="Активных РК" value={fmt(dash?.campaigns_count)} />
        </div>
      </div>

      {/* Блок 2: CRM */}
      <div className="kpi-section">
        <div className="kpi-section-label">◎ Результат (CRM)</div>
        {s.has_crm_data ? (
          <div className="kpi-grid">
            <KPICard label="MQL" value={fmt(s.total_leads)} />
            <KPICard label="CPL" value={rub(s.cpl)} />
            <KPICard label="CR клик→MQL" value={s.cr_click_lead ? pct(s.cr_click_lead*100) : '—'} />
            <KPICard label="SQL" value={fmt(s.total_sqls)} />
            <KPICard label="CPsql" value={rub(s.cpql)} />
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
        {s.has_metrika ? (
          <div className="kpi-grid">
            <KPICard label="Визиты" value={fmt(s.metrika_visits)} />
            <KPICard label="Отказы" value={pct(s.metrika_bounce_rate)} />
            <KPICard label="Глубина" value={s.metrika_page_depth?(Math.round(s.metrika_page_depth*10)/10):'—'} />
            <KPICard label="Время на сайте" value={s.metrika_avg_duration?Math.round(s.metrika_avg_duration)+'с':'—'} />
            {qs!=null && (
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
          <div className="crm-placeholder">Метрика не подключена или нет данных</div>
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
            </div>
          ) : problems.slice(0,6).map((p,i)=><SignalCard key={i} item={p}/>)}
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
              <div className="empty-desc">Точки роста появятся после сбора данных</div>
            </div>
          ) : opps.map((o,i)=><SignalCard key={i} item={o} type="opportunity"/>)}
        </div>
      </div>
    </Layout>
  )
}
