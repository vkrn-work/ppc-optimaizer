import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const PERIODS = [{key:'yesterday',label:'Вчера'},{key:'3d',label:'3 дня'},{key:'week',label:'Неделя'},{key:'month',label:'Месяц'}]

const TYPE_LABELS = {
  low_position:'📍 Позиция',traffic_drop:'📉 Трафик',zero_ctr:'👁 CTR=0',
  low_ctr:'📊 CTR',click_position_gap:'⬇ Поз.клика',scale_high_ctr:'📈 Масштаб',high_cr:'🎯 CR',
}
const PRI_LABELS = {today:'🔴 Сегодня',this_week:'🟡 Неделя',month:'🔵 Месяц',scale:'🟢 Масштаб'}

function fR(n){return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽'}

export default function Suggestions() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [period, setPeriod] = useState('week')
  const [items, setItems] = useState([])
  const [campaigns, setCampaigns] = useState([])
  const [loading, setLoading] = useState(false)
  const [taking, setTaking] = useState({})
  const [done, setDone] = useState(new Set())
  const [filters, setFilters] = useState({sev:'',priority:'',type:'',campaign:'',search:''})

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    Promise.all([
      api.getAnalyses(accountId),
      api.getCampaigns(accountId, period),
    ]).then(([analyses, camps]) => {
      const a = analyses?.[0]
      const all = [
        ...(a?.problems||[]).map(p=>({...p,_cat:'problem'})),
        ...(a?.opportunities||[]).map(o=>({...o,_cat:'opp',severity:'success'})),
      ]
      setItems(all)
      setCampaigns(Array.isArray(camps)?camps:[])
    }).catch(console.error).finally(()=>setLoading(false))
  }, [accountId, period])

  const setF = (k,v) => setFilters(f=>({...f,[k]:v}))

  const filtered = items.filter(item => {
    if (done.has(item.phrase+'_'+item.type)) return false
    if (filters.sev && item.severity!==filters.sev) return false
    if (filters.priority && item.priority!==filters.priority) return false
    if (filters.type && item.type!==filters.type) return false
    if (filters.search && !item.phrase?.toLowerCase().includes(filters.search.toLowerCase())) return false
    return true
  })

  async function takeAction(item) {
    const key = item.phrase+'_'+item.type
    setTaking(t=>({...t,[key]:true}))
    try {
      await api.createHypothesis(accountId, {
        source: 'suggestion',
        phrase: item.phrase,
        change_description: item.action,
        forecast: item.description,
        problem_type: item.type,
        keyword_id: item.keyword_id,
        severity: item.severity,
        priority: item.priority,
      })
      setDone(d=>new Set([...d, key]))
    } catch(e) {
      alert('Ошибка: '+e.message)
    } finally {
      setTaking(t=>({...t,[key]:false}))
    }
  }

  const urgentCount = items.filter(i=>i.priority==='today'&&!done.has(i.phrase+'_'+i.type)).length
  const types = [...new Set(items.map(i=>i.type).filter(Boolean))]

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div>
          <div className="page-title">Предложения</div>
          {urgentCount>0 && (
            <div style={{fontSize:12,color:'var(--red)',marginTop:2}}>
              {urgentCount} требуют внимания сегодня
            </div>
          )}
        </div>
        <div className="period-tabs">
          {PERIODS.map(p=>(
            <div key={p.key} className={`period-tab${period===p.key?' active':''}`} onClick={()=>setPeriod(p.key)}>{p.label}</div>
          ))}
        </div>
      </div>

      {/* Фильтры */}
      <div style={{display:'flex',gap:8,marginBottom:14,flexWrap:'wrap',alignItems:'center'}}>
        <input placeholder="Поиск по фразе..." value={filters.search}
          onChange={e=>setF('search',e.target.value)} style={{width:180}} />
        <select value={filters.sev} onChange={e=>setF('sev',e.target.value)} className="btn" style={{padding:'5px 10px'}}>
          <option value="">Все</option>
          <option value="critical">🔴 Критично</option>
          <option value="warning">🟡 Важно</option>
          <option value="success">🟢 Рост</option>
        </select>
        <select value={filters.priority} onChange={e=>setF('priority',e.target.value)} className="btn" style={{padding:'5px 10px'}}>
          <option value="">Все приоритеты</option>
          <option value="today">🔴 Сегодня</option>
          <option value="this_week">🟡 Неделя</option>
          <option value="month">🔵 Месяц</option>
        </select>
        <select value={filters.type} onChange={e=>setF('type',e.target.value)} className="btn" style={{padding:'5px 10px'}}>
          <option value="">Все типы</option>
          {types.map(t=><option key={t} value={t}>{TYPE_LABELS[t]||t}</option>)}
        </select>
        <select value={filters.campaign} onChange={e=>setF('campaign',e.target.value)} className="btn" style={{padding:'5px 10px'}}>
          <option value="">Все кампании</option>
          {campaigns.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {Object.values(filters).some(Boolean) && (
          <button className="btn" onClick={()=>setFilters({sev:'',priority:'',type:'',campaign:'',search:''})}>× Сбросить</button>
        )}
      </div>

      {loading ? (
        <div style={{color:'var(--text3)',fontSize:13}}>Загрузка...</div>
      ) : filtered.length===0 ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-icon">◈</div>
            <div className="empty-title">Предложений нет</div>
            <div className="empty-desc">{items.length>0?'Измените фильтры':'Запустите сбор данных для анализа'}</div>
          </div>
        </div>
      ) : (
        <div style={{display:'flex',flexDirection:'column',gap:6}}>
          {filtered.map((item,i) => {
            const sev = item.severity||'warning'
            const key = item.phrase+'_'+item.type
            const isTaking = taking[key]
            return (
              <div key={i} className={`signal-item ${sev}`} style={{cursor:'default'}}>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',gap:12}}>
                  <div style={{flex:1}}>
                    <div className="signal-header" style={{marginBottom:4}}>
                      <span className={`signal-badge ${sev}`}>
                        {sev==='critical'?'🔴 Критично':sev==='warning'?'🟡 Важно':'🟢 Рост'}
                      </span>
                      {item.priority && <span style={{fontSize:10,color:'var(--text3)'}}>{PRI_LABELS[item.priority]}</span>}
                      {item.type && <span style={{fontSize:10,background:'var(--bg4)',color:'var(--text3)',padding:'1px 5px',borderRadius:3}}>{TYPE_LABELS[item.type]||item.type}</span>}
                    </div>
                    <div className="signal-phrase">{item.phrase}</div>
                    <div className="signal-desc">{item.description}</div>
                    <div className="signal-action">→ {item.action}</div>
                    <div style={{display:'flex',gap:12,marginTop:5,fontSize:11,color:'var(--text3)',flexWrap:'wrap'}}>
                      {item.clicks!=null && <span>Кликов: <b style={{color:'var(--text1)'}}>{item.clicks}</b></span>}
                      {item.impressions!=null && <span>Показов: <b style={{color:'var(--text1)'}}>{item.impressions}</b></span>}
                      {item.avg_position!=null && <span>Позиция: <b style={{color:'var(--text1)'}}>{item.avg_position}</b></span>}
                      {item.metric_value!=null && item.type==='traffic_drop' && <span>Падение: <b style={{color:'var(--red)'}}>{item.metric_value}%</b></span>}
                      {item.spend>0 && <span>Расход: <b style={{color:'var(--text1)'}}>{fR(item.spend)}</b></span>}
                      {item.recommended_bid && <span>Рек.ставка: <b style={{color:'var(--accent)'}}>{fR(item.recommended_bid)}</b></span>}
                    </div>
                  </div>
                  <button
                    className={`btn btn-sm ${isTaking?'':'btn-primary'}`}
                    style={{flexShrink:0,marginTop:4}}
                    onClick={()=>takeAction(item)}
                    disabled={isTaking}
                  >
                    {isTaking ? '⏳' : '✓ В работу'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Layout>
  )
}
