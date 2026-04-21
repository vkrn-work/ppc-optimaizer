import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const PERIODS = [
  {key:'yesterday',label:'Вчера'},
  {key:'3d',label:'3 дня'},
  {key:'week',label:'Неделя'},
  {key:'month',label:'Месяц'},
]

const SIGNAL_LABELS = {
  low_position:        '📍 Низкая позиция',
  traffic_drop:        '📉 Падение трафика',
  epk_bid_collapse:    '⚠️ ЕПК-обвал ставок',
  spend_no_conversion: '💸 Расход без конверсий',
  cpc_spike:           '💰 Рост CPC',
  zero_ctr:            '👁 Нулевой CTR',
  low_ctr:             '📊 Низкий CTR',
  click_position_gap:  '⬇ Разрыв позиций',
  high_bounce_rate:    '↩ Высокий bounce rate',
  low_page_depth:      '📄 Малая глубина',
  low_visit_duration:  '⏱ Короткие визиты',
  mobile_quality_issue:'📱 Мобильная проблема',
  scale_opportunity:   '📈 Точка роста',
}

const LAYER_LABELS = {
  bid_keyword: '⚙ Ставки/ключи',
  impression:  '👁 Показы',
  traffic:     '🚦 Трафик',
  behavior:    '🖥 Поведение',
  opportunity: '🚀 Рост',
}

const PRI_LABELS = {
  today:     '🔴 Сегодня',
  this_week: '🟡 Эта неделя',
  month:     '🔵 Месяц',
  scale:     '🟢 Масштабирование',
}

function fR(n) { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }
function fP(n) { return (!n||n===0)?'—':(Math.round(n*10)/10) }

export default function Suggestions() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [period, setPeriod]   = useState('week')
  const [items, setItems]     = useState([])
  const [campaigns, setCampaigns] = useState([])
  const [loading, setLoading] = useState(false)
  const [taking, setTaking]   = useState({})
  const [done, setDone]       = useState(new Set())
  const [expanded, setExpanded] = useState(new Set())
  const [filters, setFilters] = useState({
    sev:'', priority:'', type:'', layer:'', search:'',
  })

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    Promise.all([
      api.getAnalyses(accountId),
      api.getCampaigns(accountId, period),
    ]).then(([analyses, camps]) => {
      const a = analyses?.[0]
      const problems = (a?.problems || []).map(p => ({...p, _cat:'problem'}))
      const opps     = (a?.opportunities || []).map(o => ({...o, _cat:'opp', severity:'info'}))
      setItems([...problems, ...opps])
      setCampaigns(Array.isArray(camps)?camps:[])
    }).catch(console.error).finally(() => setLoading(false))
  }, [accountId, period])

  const setF = (k,v) => setFilters(f=>({...f,[k]:v}))

  const filtered = items.filter(item => {
    const key = (item.phrase||item.entity_name||'')+'_'+(item.type||'')+'_'+(item.signal_id||'')
    if (done.has(key)) return false
    if (filters.sev && item.severity !== filters.sev) return false
    if (filters.priority && item.priority !== filters.priority) return false
    if (filters.type && item.type !== filters.type) return false
    if (filters.layer && item.layer !== filters.layer) return false
    if (filters.search) {
      const q = filters.search.toLowerCase()
      const txt = (item.phrase||item.entity_name||'')+' '+(item.description||'')+' '+(item.action||'')
      if (!txt.toLowerCase().includes(q)) return false
    }
    return true
  })

  const typesInData  = [...new Set(items.map(i=>i.type).filter(Boolean))]
  const layersInData = [...new Set(items.map(i=>i.layer).filter(Boolean))]

  const urgentCount  = items.filter(i =>
    i.priority==='today' &&
    !done.has((i.phrase||i.entity_name||'')+'_'+(i.type||'')+'_'+(i.signal_id||''))
  ).length

  const byPriority = {
    today:     filtered.filter(i=>i.priority==='today'),
    this_week: filtered.filter(i=>i.priority==='this_week'),
    month:     filtered.filter(i=>i.priority==='month'),
    scale:     filtered.filter(i=>i.priority==='scale' || !i.priority),
  }

  async function takeAction(item) {
    const key = (item.phrase||item.entity_name||'')+'_'+(item.type||'')+'_'+(item.signal_id||'')
    setTaking(t=>({...t,[key]:true}))
    try {
      await api.createHypothesis(accountId, {
        source:             'suggestion',
        phrase:             item.phrase || item.entity_name || '',
        change_description: item.action,
        forecast:           item.expected_outcome || item.description,
        problem_type:       item.type,
        keyword_id:         item.keyword_id,
        severity:           item.severity,
        priority:           item.priority,
      })
      setDone(d => new Set([...d, key]))
    } catch(e) {
      alert('Ошибка: ' + e.message)
    } finally {
      setTaking(t=>({...t,[key]:false}))
    }
  }

  function toggleExpanded(key) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  function SignalCard({ item }) {
    const key   = (item.phrase||item.entity_name||'')+'_'+(item.type||'')+'_'+(item.signal_id||'')
    const isOpen = expanded.has(key)
    const isTaking = taking[key]
    const sev   = item.severity || 'warning'
    const borderColor = sev==='critical'?'var(--red)':sev==='warning'?'#e07b00':sev==='info'?'var(--accent)':'var(--green)'

    return (
      <div style={{
        border: `1px solid var(--border)`,
        borderLeft: `3px solid ${borderColor}`,
        borderRadius: 8,
        padding: '12px 14px',
        background: 'var(--bg2)',
        cursor: 'pointer',
      }} onClick={() => toggleExpanded(key)}>

        {/* Заголовок */}
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',gap:8}}>
          <div style={{flex:1,minWidth:0}}>
            <div style={{display:'flex',gap:6,flexWrap:'wrap',marginBottom:4,alignItems:'center'}}>
              <span style={{fontSize:11,fontWeight:600,color:borderColor}}>
                {SIGNAL_LABELS[item.type] || item.type || '—'}
              </span>
              {item.priority && (
                <span style={{fontSize:10,color:'var(--text3)'}}>{PRI_LABELS[item.priority]}</span>
              )}
              {item.layer && (
                <span style={{fontSize:10,background:'var(--bg4)',color:'var(--text3)',
                  padding:'1px 5px',borderRadius:3}}>
                  {LAYER_LABELS[item.layer] || item.layer}
                </span>
              )}
            </div>

            {/* Объект */}
            <div style={{fontFamily:'monospace',fontSize:11,fontWeight:500,
              overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',
              maxWidth:'100%',color:'var(--text1)',marginBottom:4}}
              title={item.phrase||item.entity_name}>
              {item.phrase || item.entity_name || '—'}
            </div>

            {/* Проблема */}
            <div style={{fontSize:12,color:'var(--text2)',marginBottom:2}}>
              {item.description}
            </div>

            {/* Действие */}
            <div style={{fontSize:12,color:'var(--accent)',fontWeight:500}}>
              → {item.action}
            </div>
          </div>

          {/* Кнопка действия */}
          <button
            className={`btn btn-sm${isTaking?'':' btn-primary'}`}
            style={{flexShrink:0,marginTop:2}}
            onClick={e=>{e.stopPropagation();takeAction(item)}}
            disabled={isTaking}
          >
            {isTaking?'⏳':'✓ В работу'}
          </button>
        </div>

        {/* Метрики */}
        <div style={{display:'flex',gap:12,marginTop:6,fontSize:11,color:'var(--text3)',flexWrap:'wrap'}}>
          {item.clicks!=null    && <span>Кл: <b style={{color:'var(--text1)'}}>{item.clicks}</b></span>}
          {item.avg_position!=null && <span>Поз: <b style={{color:'var(--text1)'}}>{fP(item.avg_position)}</b></span>}
          {item.traffic_volume!=null && <span>Объём: <b style={{color:'var(--text1)'}}>{item.traffic_volume}</b></span>}
          {item.spend>0         && <span>Расход: <b style={{color:'var(--text1)'}}>{fR(item.spend)}</b></span>}
          {item.metric_value!=null && item.type==='traffic_drop' &&
            <span>Падение: <b style={{color:'var(--red)'}}>{item.metric_value}%</b></span>}
          {item.recommended_bid && <span>Рек.ставка: <b style={{color:'var(--accent)'}}>{fR(item.recommended_bid)}</b></span>}
          {item.metric_value!=null && item.type==='low_position' &&
            <span>Позиция: <b style={{color:'var(--red)'}}>{item.metric_value}</b></span>}
        </div>

        {/* Развёрнутые детали */}
        {isOpen && (
          <div style={{marginTop:10,paddingTop:10,borderTop:'1px solid var(--border)'}}
            onClick={e=>e.stopPropagation()}>
            {item.hypothesis && (
              <div style={{fontSize:12,marginBottom:6}}>
                <span style={{color:'var(--text3)',fontWeight:500}}>Гипотеза: </span>
                <span style={{color:'var(--text2)'}}>{item.hypothesis}</span>
              </div>
            )}
            {item.expected_outcome && (
              <div style={{fontSize:12,marginBottom:6}}>
                <span style={{color:'var(--text3)',fontWeight:500}}>Ожидаем: </span>
                <span style={{color:'var(--text2)'}}>{item.expected_outcome}</span>
              </div>
            )}
            {item.calculation_logic && (
              <div style={{fontSize:11,fontFamily:'monospace',color:'var(--text3)',
                background:'var(--bg4)',padding:'6px 8px',borderRadius:4,marginBottom:6}}>
                {item.calculation_logic}
              </div>
            )}
            {/* ЕПК-обвал: список ключей */}
            {item.type==='epk_bid_collapse' && item.collapsed_keywords && (
              <div style={{marginTop:6}}>
                <div style={{fontSize:11,color:'var(--text3)',marginBottom:4}}>
                  Примеры просевших ключей:
                </div>
                {item.collapsed_keywords.slice(0,5).map((kw,i) => (
                  <div key={i} style={{fontSize:10,fontFamily:'monospace',
                    color:'var(--text2)',padding:'2px 0'}}>
                    {kw.phrase}: {kw.prev_bid}₽ → {kw.curr_bid}₽
                    (было {kw.prev_clicks} кл., стало {kw.curr_clicks})
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  const anyFilters = Object.values(filters).some(Boolean)

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div>
          <div className="page-title">Предложения</div>
          {urgentCount > 0 && (
            <div style={{fontSize:12,color:'var(--red)',marginTop:2}}>
              {urgentCount} требуют внимания сегодня
            </div>
          )}
        </div>
        <div className="period-tabs">
          {PERIODS.map(p=>(
            <div key={p.key} className={`period-tab${period===p.key?' active':''}`}
              onClick={()=>setPeriod(p.key)}>{p.label}</div>
          ))}
        </div>
      </div>

      {/* Фильтры */}
      <div style={{display:'flex',gap:8,marginBottom:14,flexWrap:'wrap',alignItems:'center'}}>
        <input placeholder="Поиск по ключу или описанию..." value={filters.search}
          onChange={e=>setF('search',e.target.value)} style={{width:220}} />

        <select value={filters.sev} onChange={e=>setF('sev',e.target.value)}
          className="btn" style={{padding:'5px 10px'}}>
          <option value="">Все критичности</option>
          <option value="critical">🔴 Критично</option>
          <option value="warning">🟡 Важно</option>
          <option value="info">🔵 Инфо</option>
        </select>

        <select value={filters.priority} onChange={e=>setF('priority',e.target.value)}
          className="btn" style={{padding:'5px 10px'}}>
          <option value="">Все приоритеты</option>
          <option value="today">🔴 Сегодня</option>
          <option value="this_week">🟡 Эта неделя</option>
          <option value="month">🔵 Месяц</option>
          <option value="scale">🟢 Масштаб</option>
        </select>

        <select value={filters.type} onChange={e=>setF('type',e.target.value)}
          className="btn" style={{padding:'5px 10px'}}>
          <option value="">Все типы</option>
          {typesInData.map(t=>(
            <option key={t} value={t}>{SIGNAL_LABELS[t]||t}</option>
          ))}
        </select>

        <select value={filters.layer} onChange={e=>setF('layer',e.target.value)}
          className="btn" style={{padding:'5px 10px'}}>
          <option value="">Все уровни</option>
          {layersInData.map(l=>(
            <option key={l} value={l}>{LAYER_LABELS[l]||l}</option>
          ))}
        </select>

        {anyFilters && (
          <button className="btn"
            onClick={()=>setFilters({sev:'',priority:'',type:'',layer:'',search:''})}>
            × Сбросить
          </button>
        )}

        <span style={{fontSize:11,color:'var(--text3)',marginLeft:'auto'}}>
          {filtered.length} из {items.length}
        </span>
      </div>

      {loading ? (
        <div style={{color:'var(--text3)',fontSize:13,padding:'2rem',textAlign:'center'}}>
          Загрузка...
        </div>
      ) : items.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-icon">◈</div>
            <div className="empty-title">Предложений нет</div>
            <div className="empty-desc">Запустите сбор данных для анализа</div>
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{padding:'2rem',textAlign:'center',color:'var(--text3)'}}>
          Нет предложений по выбранным фильтрам
        </div>
      ) : (
        /* Группировка по приоритету */
        <div style={{display:'flex',flexDirection:'column',gap:20}}>
          {[
            {key:'today',     items:byPriority.today},
            {key:'this_week', items:byPriority.this_week},
            {key:'month',     items:byPriority.month},
            {key:'scale',     items:byPriority.scale},
          ].filter(g=>g.items.length>0).map(group => (
            <div key={group.key}>
              <div style={{fontSize:13,fontWeight:600,marginBottom:8,color:'var(--text2)'}}>
                {PRI_LABELS[group.key]} — {group.items.length} {group.items.length===1?'предложение':'предложений'}
              </div>
              <div style={{display:'flex',flexDirection:'column',gap:6}}>
                {group.items.map((item,i) => (
                  <SignalCard key={i} item={item} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Layout>
  )
}
