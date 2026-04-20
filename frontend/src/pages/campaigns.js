import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const PERIODS = [{key:'yesterday',label:'Вчера'},{key:'3d',label:'3 дня'},{key:'week',label:'Неделя'},{key:'month',label:'Месяц'}]

function fN(n)  { return (n==null||n==='')?'—':n>=1000?Math.round(n).toLocaleString('ru'):Math.round(n*10)/10 }
function fR(n)  { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }
function fP(n)  { return (!n||n===0)?'—':(Math.round(n*10)/10) }
function fPct(n){ return n==null?'—':(Math.round(n*10)/10)+'%' }

function Delta({ v }) {
  if (v == null) return <span style={{color:'var(--text3)',fontSize:10}}>—</span>
  const up = v > 0
  return (
    <span style={{fontSize:10,color:up?'var(--green)':'var(--red)',marginLeft:4}}>
      {up?'▲':'▼'}{Math.abs(v)}%
    </span>
  )
}

function PosCell({ v }) {
  if (!v) return <span>—</span>
  const color = v > 3 ? 'var(--red)' : v < 2 ? 'var(--green)' : 'inherit'
  return <span style={{color,fontWeight:v>3?600:400}}>{fP(v)}</span>
}

const COLS = [
  { key:'spend',             label:'Расход',        fmt:fR,    invert:true  },
  { key:'impressions',       label:'Показы',        fmt:fN               },
  { key:'clicks',            label:'Клики',         fmt:fN               },
  { key:'ctr',               label:'CTR',           fmt:fPct             },
  { key:'avg_cpc',           label:'CPC',           fmt:fR,    invert:true  },
  { key:'avg_position',      label:'Поз. показа',   fmt:fP,    invert:true  },
  { key:'avg_click_position',label:'Поз. клика',    fmt:fP,    invert:true  },
  { key:'traffic_volume',    label:'Ср. объём тр.', fmt:fN               },
]

export default function Campaigns() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [period, setPeriod] = useState('week')
  const [view, setView] = useState('campaigns')
  const [data, setData] = useState([])
  const [campaigns, setCampaigns] = useState([])
  const [loading, setLoading] = useState(false)
  const [onlyActive, setOnlyActive] = useState(true)
  const [search, setSearch] = useState('')
  const [selCampaign, setSelCampaign] = useState('')
  const [sortBy, setSortBy] = useState('spend')
  const [sortDir, setSortDir] = useState(-1)

  useEffect(() => {
    if (!accountId) return
    setLoading(true)

    // ВАЖНО: передаём period в getCampaigns
    Promise.all([
      api.getCampaigns(accountId, period, onlyActive),
      view === 'keywords'
        ? api.getKeywords(accountId, `?period=${period}${onlyActive?'&active_only=true':''}${selCampaign?'&campaign_id='+selCampaign:''}${search?'&search='+encodeURIComponent(search):''}`)
        : Promise.resolve(null),
    ]).then(([camps, kws]) => {
      const list = onlyActive ? (camps||[]).filter(c => c.is_active) : (camps||[])
      setCampaigns(list)
      if (view === 'campaigns') setData(list)
      else if (view === 'keywords' && kws) setData(kws)
    }).catch(console.error).finally(() => setLoading(false))
  }, [accountId, period, view, onlyActive, selCampaign, search])

  function toggleSort(key) {
    if (sortBy === key) setSortDir(d => -d)
    else { setSortBy(key); setSortDir(-1) }
  }

  const filtered = data
    .filter(item => !search || (item.name||item.phrase||'').toLowerCase().includes(search.toLowerCase()))
    .sort((a,b) => ((b[sortBy]||0) - (a[sortBy]||0)) * sortDir)

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      {/* Header */}
      <div className="page-header">
        <div className="page-title">По кампаниям</div>
        <div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
          <div className="period-tabs">
            {PERIODS.map(p=>(
              <div key={p.key} className={`period-tab${period===p.key?' active':''}`}
                onClick={()=>setPeriod(p.key)}>{p.label}</div>
            ))}
          </div>
          <div className="period-tabs">
            {['campaigns','keywords'].map(v=>(
              <div key={v} className={`period-tab${view===v?' active':''}`}
                onClick={()=>setView(v)}>
                {v==='campaigns'?'Кампании':'Ключевые слова'}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Filters */}
      <div style={{display:'flex',gap:8,marginBottom:12,flexWrap:'wrap',alignItems:'center'}}>
        <input
          placeholder={`Поиск по ${view==='campaigns'?'кампании':'фразе'}...`}
          value={search} onChange={e=>setSearch(e.target.value)}
          style={{width:200}}
        />
        {view==='keywords' && (
          <select value={selCampaign} onChange={e=>setSelCampaign(e.target.value)}
            className="btn" style={{padding:'5px 10px'}}>
            <option value="">Все кампании</option>
            {campaigns.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        )}
        <button
          className={`btn${onlyActive?' btn-primary':''}`}
          onClick={()=>setOnlyActive(a=>!a)}
        >
          {onlyActive?'✓ ':''} Только активные
        </button>
        <span style={{fontSize:11,color:'var(--text3)',marginLeft:4}}>
          {filtered.length} {view==='campaigns'?'кампаний':'ключей'}
        </span>
      </div>

      {/* Table */}
      <div className="card" style={{padding:0,overflow:'auto'}}>
        {loading ? (
          <div style={{padding:'2rem',textAlign:'center',color:'var(--text3)'}}>Загрузка...</div>
        ) : filtered.length === 0 ? (
          <div style={{padding:'2rem',textAlign:'center',color:'var(--text3)'}}>
            Нет данных{onlyActive?' — попробуйте снять «Только активные»':''}
          </div>
        ) : view === 'campaigns' ? (
          <table>
            <thead>
              <tr>
                <th style={{minWidth:220}}>Кампания</th>
                <th>ID Директа</th>
                <th>Тип</th>
                <th>Стратегия</th>
                {COLS.map(c=>(
                  <th key={c.key} style={{cursor:'pointer',whiteSpace:'nowrap'}}
                    onClick={()=>toggleSort(c.key)}>
                    {c.label} {sortBy===c.key?(sortDir>0?'↑':'↓'):''}
                  </th>
                ))}
                {/* CRM плейсхолдер */}
                <th style={{color:'var(--text3)'}}>SQL</th>
                <th style={{color:'var(--text3)'}}>CPL</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(c=>(
                <tr key={c.id}>
                  <td style={{fontWeight:500,fontSize:12,maxWidth:260}}>
                    <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                      {c.name}
                    </div>
                    {!c.is_active && (
                      <span style={{fontSize:10,color:'var(--text3)'}}>остановлена</span>
                    )}
                  </td>
                  <td style={{fontSize:11,color:'var(--text3)',fontFamily:'monospace'}}>
                    {c.direct_id || '—'}
                  </td>
                  <td style={{fontSize:11,color:'var(--text3)'}}>
                    {c.campaign_type || '—'}
                  </td>
                  <td>
                    <span className={`badge ${c.strategy_type==='MANUAL_CPC'?'badge-ok':'badge-info'}`}>
                      {c.strategy_type==='MANUAL_CPC'?'Ручная':c.strategy_type||'—'}
                    </span>
                  </td>
                  {COLS.map(col=>(
                    <td key={col.key}>
                      {col.key==='avg_position'||col.key==='avg_click_position'
                        ? <PosCell v={c[col.key]} />
                        : col.fmt(c[col.key])}
                    </td>
                  ))}
                  <td style={{color:'var(--text3)',fontSize:11}}>—</td>
                  <td style={{color:'var(--text3)',fontSize:11}}>—</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          /* Keywords table */
          <table>
            <thead>
              <tr>
                <th style={{minWidth:220}}>Фраза</th>
                <th>Ставка</th>
                {COLS.map(c=>(
                  <th key={c.key} style={{cursor:'pointer',whiteSpace:'nowrap'}}
                    onClick={()=>toggleSort(c.key)}>
                    {c.label} {sortBy===c.key?(sortDir>0?'↑':'↓'):''}
                  </th>
                ))}
                <th>Δ клики</th>
                <th>Проблема</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0,300).map(kw=>(
                <tr key={kw.id} style={kw.problem?{background:'rgba(255,79,79,0.03)'}:{}}>
                  <td style={{fontFamily:'monospace',fontSize:11,maxWidth:260}}>
                    <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{kw.phrase}</div>
                    {kw.status && kw.status!=='ACTIVE' && (
                      <span style={{fontSize:10,color:'var(--text3)'}}>· {kw.status}</span>
                    )}
                  </td>
                  <td style={{whiteSpace:'nowrap'}}>{kw.current_bid?fR(kw.current_bid):'—'}</td>
                  {COLS.map(col=>(
                    <td key={col.key}>
                      {col.key==='avg_position'||col.key==='avg_click_position'
                        ? <PosCell v={kw[col.key]} />
                        : col.fmt(kw[col.key])}
                    </td>
                  ))}
                  <td>
                    {kw.click_delta!=null && (
                      <span style={{fontSize:11,color:kw.click_delta>0?'var(--green)':'var(--red)'}}>
                        {kw.click_delta>0?'▲':'▼'}{Math.abs(kw.click_delta)}%
                      </span>
                    )}
                  </td>
                  <td>
                    {kw.problem && (
                      <span className="badge badge-warn" style={{fontSize:10}}>
                        {{low_position:'📍 Поз',traffic_drop:'📉 Тр.',zero_ctr:'👁 CTR',
                          low_ctr:'📊 CTR',click_position_gap:'⬇ Поз.кл'}[kw.problem.type]||kw.problem.type}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
              {filtered.length>300 && (
                <tr><td colSpan={COLS.length+4} style={{textAlign:'center',color:'var(--text3)',padding:'1rem',fontSize:12}}>
                  Показано 300 из {filtered.length}
                </td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </Layout>
  )
}
