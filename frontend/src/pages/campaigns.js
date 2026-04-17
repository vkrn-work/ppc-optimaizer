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

function f(n, suffix='') { return n==null?'—':typeof n==='number'?Math.round(n*10)/10+suffix:n+suffix }
function fR(n) { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }
function fP(n) { return (!n||n===0)?'—':(Math.round(n*10)/10) }

const PROBLEM_LABELS = {
  low_position:'📍 Позиция',traffic_drop:'📉 Трафик',
  zero_ctr:'👁 CTR=0',low_ctr:'📊 CTR низкий',click_position_gap:'⬇ Поз.клика',
}

export default function Campaigns() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [period, setPeriod] = useState('week')
  const [view, setView] = useState('campaigns')
  const [campaigns, setCampaigns] = useState([])
  const [keywords, setKeywords] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [selCampaign, setSelCampaign] = useState('')

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    const loads = [api.getCampaigns(accountId, period)]
    if (view === 'keywords') loads.push(api.getKeywords(accountId, `?period=${period}${selCampaign?'&campaign_id='+selCampaign:''}${search?'&search='+search:''}`))
    Promise.all(loads)
      .then(([c, k]) => { setCampaigns(Array.isArray(c)?c:[]); if(k) setKeywords(Array.isArray(k)?k:[]) })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [accountId, period, view, selCampaign, search])

  const filteredC = campaigns.filter(c => !search || c.name?.toLowerCase().includes(search.toLowerCase()))

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">По кампаниям</div>
        <div style={{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
          <input placeholder="Поиск..." value={search} onChange={e=>setSearch(e.target.value)} style={{width:160}} />
          {view==='keywords' && (
            <select value={selCampaign} onChange={e=>setSelCampaign(e.target.value)} className="btn" style={{padding:'5px 10px'}}>
              <option value="">Все кампании</option>
              {campaigns.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          )}
          <div className="period-tabs">
            {PERIODS.map(p=>(
              <div key={p.key} className={`period-tab${period===p.key?' active':''}`} onClick={()=>setPeriod(p.key)}>{p.label}</div>
            ))}
          </div>
          <div className="period-tabs">
            <div className={`period-tab${view==='campaigns'?' active':''}`} onClick={()=>setView('campaigns')}>Кампании</div>
            <div className={`period-tab${view==='keywords'?' active':''}`} onClick={()=>setView('keywords')}>Ключи</div>
          </div>
        </div>
      </div>

      {loading ? (
        <div style={{color:'var(--text3)',fontSize:13}}>Загрузка...</div>
      ) : view === 'campaigns' ? (
        <div className="card" style={{padding:0,overflow:'auto'}}>
          <table>
            <thead>
              <tr>
                <th style={{minWidth:200}}>Кампания</th>
                <th>Расход</th>
                <th>Клики</th>
                <th>CTR</th>
                <th>Позиция</th>
                <th>Трафик</th>
                <th>Стратегия</th>
              </tr>
            </thead>
            <tbody>
              {filteredC.length===0 ? (
                <tr><td colSpan={7} style={{textAlign:'center',color:'var(--text3)',padding:'2rem'}}>
                  {campaigns.length===0 ? 'Нет данных — запустите сбор' : 'Нет совпадений'}
                </td></tr>
              ) : filteredC.map(c=>(
                <tr key={c.id}>
                  <td style={{fontWeight:500,fontSize:12}}>
                    <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',maxWidth:260}}>{c.name}</div>
                  </td>
                  <td>{fR(c.spend)}</td>
                  <td>{f(c.clicks)}</td>
                  <td>{c.ctr ? f(c.ctr,'%') : '—'}</td>
                  <td>{fP(c.avg_position)}</td>
                  <td>{f(c.traffic_volume)}</td>
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
      ) : (
        <div className="card" style={{padding:0,overflow:'auto'}}>
          <table>
            <thead>
              <tr>
                <th style={{minWidth:220}}>Фраза</th>
                <th>Ставка</th>
                <th>Клики</th>
                <th>Δ клики</th>
                <th>Расход</th>
                <th>Позиция</th>
                <th>Проблема</th>
              </tr>
            </thead>
            <tbody>
              {keywords.length===0 ? (
                <tr><td colSpan={7} style={{textAlign:'center',color:'var(--text3)',padding:'2rem'}}>Нет данных</td></tr>
              ) : keywords.slice(0,300).map(kw=>(
                <tr key={kw.id}>
                  <td style={{fontFamily:'monospace',fontSize:11,maxWidth:280}}>
                    <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{kw.phrase}</div>
                  </td>
                  <td>{kw.current_bid ? fR(kw.current_bid) : '—'}</td>
                  <td>{f(kw.clicks)}</td>
                  <td>
                    {kw.click_delta != null && (
                      <span style={{color:kw.click_delta>0?'var(--green)':'var(--red)',fontSize:11}}>
                        {kw.click_delta>0?'▲':'▼'}{Math.abs(kw.click_delta)}%
                      </span>
                    )}
                  </td>
                  <td>{fR(kw.spend)}</td>
                  <td>{fP(kw.avg_position)}</td>
                  <td>
                    {kw.problem && (
                      <span className="badge badge-warn" style={{fontSize:10}}>
                        {PROBLEM_LABELS[kw.problem.type] || kw.problem.type}
                      </span>
                    )}
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
