import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

// «Полный анализ» — объединяет три ранее отдельные страницы (По кампаниям,
// Ставки, Корректировки) под одной вкладкой, чтобы не плодить пункты меню.
// Каждая секция — почти дословно перенесённое тело старой страницы, только
// без собственного <Layout> и useAccount() (accountId приходит пропсом).
// Старые файлы campaigns.js / bids.js / adjustments.js / keywords.js остаются
// в репозитории, но больше не в навигации.

const TABS = [
  { key: 'campaigns',   label: 'Кампании и ключи' },
  { key: 'bids',        label: 'Ставки' },
  { key: 'adjustments', label: 'Корректировки' },
]

export default function Analysis() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [tab, setTab] = useState('campaigns')

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Полный анализ</div>
        <div className="period-tabs">
          {TABS.map(t => (
            <div key={t.key} className={`period-tab${tab===t.key?' active':''}`}
              onClick={()=>setTab(t.key)}>{t.label}</div>
          ))}
        </div>
      </div>

      <div style={{ display: tab==='campaigns' ? 'block' : 'none' }}>
        <CampaignsSection accountId={accountId} />
      </div>
      <div style={{ display: tab==='bids' ? 'block' : 'none' }}>
        <BidsSection accountId={accountId} />
      </div>
      <div style={{ display: tab==='adjustments' ? 'block' : 'none' }}>
        <AdjustmentsSection accountId={accountId} />
      </div>
    </Layout>
  )
}

// ═══════════════════════════════════════════════════════════════════════
// Кампании и ключи (бывш. campaigns.js)
// ═══════════════════════════════════════════════════════════════════════

function CampaignsSection({ accountId }) {
  const PERIODS = [
    {key:'yesterday',label:'Вчера'},
    {key:'3d',label:'3 дня'},
    {key:'week',label:'Неделя'},
    {key:'month',label:'Месяц'},
    {key:'custom',label:'Период ↓'},
  ]

  function fN(n)   { return (n==null||n==='')?'—':n>=1000?Math.round(n).toLocaleString('ru'):Math.round(n*10)/10 }
  function fR(n)   { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }
  function fP(n)   { return (!n||n===0)?'—':(Math.round(n*10)/10) }
  function fPct(n) { return n==null?'—':(Math.round(n*10)/10)+'%' }

  function Delta({ v, invert }) {
    if (v==null) return <span style={{color:'var(--text3)',fontSize:10}}>—</span>
    const up   = v.value > 0
    const good = invert ? !up : up
    return (
      <span style={{fontSize:10,color:good?'var(--green)':'var(--red)',marginLeft:4}}>
        {up?'▲':'▼'}{Math.abs(v.value)}%
      </span>
    )
  }

  function PosCell({ v }) {
    if (!v) return <span>—</span>
    const color = v>3?'var(--red)':v<2?'var(--green)':'inherit'
    return <span style={{color,fontWeight:v>3?600:400}}>{fP(v)}</span>
  }

  const SIGNAL_LABELS = {
    low_position:        '📍 Позиция',
    traffic_drop:        '📉 Трафик',
    epk_bid_collapse:    '⚠️ ЕПК-обвал',
    spend_no_conversion: '💸 Без конверсий',
    cpc_spike:           '💰 Рост CPC',
    zero_ctr:            '👁 CTR=0',
    low_ctr:             '📊 Низкий CTR',
    click_position_gap:  '⬇ Разрыв поз.',
    high_bounce_rate:    '↩ Bounce',
    scale_opportunity:   '📈 Рост',
  }

  const COLS = [
    { key:'spend',              label:'Расход',       fmt:fR,   invert:true },
    { key:'impressions',        label:'Показы',       fmt:fN               },
    { key:'clicks',             label:'Клики',        fmt:fN               },
    { key:'ctr',                label:'CTR',          fmt:fPct             },
    { key:'avg_cpc',            label:'CPC',          fmt:fR,   invert:true },
    { key:'avg_position',       label:'Поз. показа',  fmt:fP,   invert:true },
    { key:'avg_click_position', label:'Поз. клика',   fmt:fP,   invert:true },
    { key:'traffic_volume',     label:'Объём тр.',    fmt:fN               },
  ]

  function CampaignDrillDown({ campaign, accountId, dateFrom, dateTo }) {
    const [rows, setRows] = useState(null)
    const [loading, setLoading] = useState(false)

    useEffect(() => {
      if (!accountId || !campaign || !dateFrom || !dateTo) return
      setLoading(true)
      api.getCampaignDailyStats(accountId, campaign.id, dateFrom, dateTo)
        .then(r => setRows(r.rows || []))
        .catch(console.error)
        .finally(() => setLoading(false))
    }, [accountId, campaign?.id, dateFrom, dateTo])

    if (loading) return <tr><td colSpan={COLS.length + 4} style={{padding:'8px 16px',color:'var(--text3)'}}>Загрузка...</td></tr>
    if (!rows?.length) return <tr><td colSpan={COLS.length + 4} style={{padding:'8px 16px',color:'var(--text3)'}}>Нет данных за период</td></tr>

    return (
      <tr>
        <td colSpan={COLS.length + 4} style={{padding:0}}>
          <div style={{background:'var(--bg2)',padding:'8px 16px',borderTop:'1px solid var(--border)'}}>
            <div style={{fontSize:11,color:'var(--text3)',marginBottom:6}}>Динамика по дням</div>
            <div style={{overflowX:'auto'}}>
              <table style={{fontSize:11}}>
                <thead>
                  <tr>
                    <th>Дата</th><th>Клики</th><th>Показы</th>
                    <th>Расход</th><th>CPC</th><th>CTR</th><th>Позиция</th><th>Объём тр.</th>
                  </tr>
                </thead>
                <tbody>
                  {[...rows].reverse().map((d,i) => (
                    <tr key={i}>
                      <td style={{whiteSpace:'nowrap'}}>
                        {new Date(d.date).toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit'})}
                      </td>
                      <td style={{fontWeight:i===0?600:400}}>{fN(d.clicks)}</td>
                      <td>{fN(d.impressions)}</td>
                      <td>{fR(d.spend)}</td>
                      <td>{fR(d.avg_cpc)}</td>
                      <td>{fPct(d.ctr)}</td>
                      <td><PosCell v={d.avg_position} /></td>
                      <td>{fN(d.traffic_volume)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </td>
      </tr>
    )
  }

  const [period, setPeriod]           = useState('week')
  const [customFrom, setCustomFrom]   = useState('')
  const [customTo, setCustomTo]       = useState('')
  const [compareFrom, setCompareFrom] = useState('')
  const [compareTo, setCompareTo]     = useState('')
  const [showCustom, setShowCustom]   = useState(false)
  const [view, setView]               = useState('campaigns')
  const [data, setData]               = useState([])
  const [campaigns, setCampaigns]     = useState([])
  const [loading, setLoading]         = useState(false)
  const [onlyActive, setOnlyActive]   = useState(true)
  const [search, setSearch]           = useState('')
  const [selCampaign, setSelCampaign] = useState('')
  const [sortBy, setSortBy]           = useState('signals_critical')
  const [sortDir, setSortDir]         = useState(-1)
  const [expandedId, setExpandedId]   = useState(null)

  const [activeDateFrom, setActiveDateFrom] = useState('')
  const [activeDateTo, setActiveDateTo]     = useState('')

  function buildExtra() {
    if (period === 'custom' && customFrom && customTo) {
      let e = `date_from=${customFrom}&date_to=${customTo}`
      if (compareFrom && compareTo) e += `&compare_from=${compareFrom}&compare_to=${compareTo}`
      return e
    }
    return ''
  }

  function loadData() {
    if (!accountId) return
    setLoading(true)
    const extra = buildExtra()
    Promise.all([
      api.getCampaigns(accountId, period === 'custom' ? 'week' : period, onlyActive, extra),
      view === 'keywords'
        ? api.getKeywords(accountId, `?period=${period === 'custom' ? 'week' : period}${onlyActive?'&active_only=true':''}${selCampaign?'&campaign_id='+selCampaign:''}${search?'&search='+encodeURIComponent(search):''}${extra?'&'+extra:''}`)
        : Promise.resolve(null),
    ]).then(([camps, kws]) => {
      const list = onlyActive ? (camps||[]).filter(c => c.is_active) : (camps||[])
      setCampaigns(list)
      if (view === 'campaigns') setData(list)
      else if (view === 'keywords' && kws) setData(Array.isArray(kws) ? kws : [])
      if (period !== 'custom') {
        const today = new Date()
        const daysMap = {yesterday:1,'3d':3,week:7,month:30}
        const days = daysMap[period] || 7
        const from = new Date(today); from.setDate(from.getDate() - days)
        setActiveDateFrom(from.toISOString().slice(0,10))
        setActiveDateTo(today.toISOString().slice(0,10))
      } else {
        setActiveDateFrom(customFrom)
        setActiveDateTo(customTo)
      }
    }).catch(console.error).finally(() => setLoading(false))
  }

  useEffect(() => {
    if (period !== 'custom') loadData()
  }, [accountId, period, view, onlyActive, selCampaign, search])

  function toggleSort(key) {
    if (sortBy === key) setSortDir(d => -d)
    else { setSortBy(key); setSortDir(-1) }
  }

  const filtered = data
    .filter(item => !search || (item.name||item.phrase||'').toLowerCase().includes(search.toLowerCase()))
    .sort((a,b) => {
      if (sortBy === 'signals_critical') {
        const aS = (a.signals_critical||0) > 0 ? 0 : (a.signals_count||0) > 0 ? 1 : 2
        const bS = (b.signals_critical||0) > 0 ? 0 : (b.signals_count||0) > 0 ? 1 : 2
        if (aS !== bS) return aS - bS
        return ((b.spend||0) - (a.spend||0)) * sortDir
      }
      return ((b[sortBy]||0) - (a[sortBy]||0)) * sortDir
    })

  function strategyLabel(s) {
    if (s === 'MANUAL_CPC') return {label:'Ручная', cls:'badge-ok'}
    if (s === 'AUTO')       return {label:'Авто',   cls:'badge-info'}
    return {label: s || '—', cls:'badge-info'}
  }

  return (
    <>
      <div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center',marginBottom:14}}>
        <div className="period-tabs">
          {PERIODS.map(p=>(
            <div key={p.key} className={`period-tab${period===p.key?' active':''}`}
              onClick={()=>{ setPeriod(p.key); setShowCustom(p.key==='custom') }}>
              {p.label}
            </div>
          ))}
        </div>
        <div className="period-tabs">
          {['campaigns','keywords'].map(v=>(
            <div key={v} className={`period-tab${view===v?' active':''}`} onClick={()=>setView(v)}>
              {v==='campaigns'?'Кампании':'Ключи'}
            </div>
          ))}
        </div>
      </div>

      {showCustom && (
        <div className="card" style={{marginBottom:14,padding:'12px 16px'}}>
          <div style={{display:'flex',gap:12,flexWrap:'wrap',alignItems:'flex-end'}}>
            <div>
              <div style={{fontSize:11,color:'var(--text3)',marginBottom:4}}>Период анализа</div>
              <div style={{display:'flex',gap:6,alignItems:'center'}}>
                <input type="date" value={customFrom} onChange={e=>setCustomFrom(e.target.value)} style={{padding:'4px 8px'}} />
                <span style={{color:'var(--text3)'}}>—</span>
                <input type="date" value={customTo} onChange={e=>setCustomTo(e.target.value)} style={{padding:'4px 8px'}} />
              </div>
            </div>
            <div>
              <div style={{fontSize:11,color:'var(--text3)',marginBottom:4}}>Период сравнения</div>
              <div style={{display:'flex',gap:6,alignItems:'center'}}>
                <input type="date" value={compareFrom} onChange={e=>setCompareFrom(e.target.value)} style={{padding:'4px 8px'}} />
                <span style={{color:'var(--text3)'}}>—</span>
                <input type="date" value={compareTo} onChange={e=>setCompareTo(e.target.value)} style={{padding:'4px 8px'}} />
              </div>
            </div>
            <button className="btn btn-primary" onClick={loadData} disabled={!customFrom||!customTo||loading}>
              Применить
            </button>
          </div>
        </div>
      )}

      <div style={{display:'flex',gap:8,marginBottom:12,flexWrap:'wrap',alignItems:'center'}}>
        <input placeholder="Поиск..." value={search} onChange={e=>setSearch(e.target.value)} style={{width:200}} />
        {view==='keywords' && (
          <select value={selCampaign} onChange={e=>setSelCampaign(e.target.value)}
            className="btn" style={{padding:'5px 10px'}}>
            <option value="">Все кампании</option>
            {campaigns.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        )}
        <button className={`btn${onlyActive?' btn-primary':''}`} onClick={()=>setOnlyActive(a=>!a)}>
          {onlyActive?'✓ ':''} Только активные
        </button>
        <span style={{fontSize:11,color:'var(--text3)',marginLeft:4}}>
          {filtered.length} {view==='campaigns'?'кампаний':'ключей'}
        </span>
      </div>

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
                <th>ID</th>
                <th>Стратегия</th>
                {COLS.map(c=>(
                  <th key={c.key} style={{cursor:'pointer',whiteSpace:'nowrap'}}
                    onClick={()=>toggleSort(c.key)}>
                    {c.label}{sortBy===c.key?(sortDir>0?' ↑':' ↓'):''}
                  </th>
                ))}
                <th>Bounce</th>
                <th style={{cursor:'pointer'}} onClick={()=>toggleSort('signals_critical')}>
                  Сигналы{sortBy==='signals_critical'?' ↓':''}
                </th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(c => {
                const strat      = strategyLabel(c.strategy_type)
                const isExpanded = expandedId === c.id
                const hasCritical = (c.signals_critical||0) > 0
                const rowBg = hasCritical
                  ? 'rgba(255,79,79,0.04)'
                  : c.has_epk_collapse
                  ? 'rgba(255,165,0,0.04)'
                  : undefined
                return (
                  <>
                    <tr key={c.id} style={{cursor:'pointer',background:rowBg}}
                      onClick={()=>setExpandedId(isExpanded?null:c.id)}>
                      <td style={{fontWeight:500,fontSize:12,maxWidth:260}}>
                        <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{c.name}</div>
                        {!c.is_active && <span style={{fontSize:10,color:'var(--text3)'}}>остановлена</span>}
                        {c.has_epk_collapse && <span style={{fontSize:10,color:'#e07b00',marginLeft:4}}>⚠ ЕПК</span>}
                      </td>
                      <td style={{fontSize:11,color:'var(--text3)',fontFamily:'monospace'}}>{c.direct_id||'—'}</td>
                      <td><span className={`badge ${strat.cls}`}>{strat.label}</span></td>
                      {COLS.map(col => (
                        <td key={col.key}>
                          {col.key==='avg_position'||col.key==='avg_click_position'
                            ? <PosCell v={c[col.key]} />
                            : (
                              <div>
                                {col.fmt(c[col.key])}
                                {c[`delta_${col.key.replace('avg_','')}`] && (
                                  <Delta v={c[`delta_${col.key.replace('avg_','')}`]} invert={col.invert} />
                                )}
                                {c[`prev_${col.key}`] != null && (
                                  <div style={{fontSize:9,color:'var(--text3)'}}>
                                    пред: {col.fmt(c[`prev_${col.key}`])}
                                  </div>
                                )}
                              </div>
                            )}
                        </td>
                      ))}
                      <td style={{fontSize:11,color:c.bounce_rate>65?'var(--red)':c.bounce_rate>50?'#e07b00':'inherit'}}>
                        {c.bounce_rate != null ? `${c.bounce_rate}%` : '—'}
                      </td>
                      <td>
                        {c.signals_count > 0 ? (
                          <div style={{display:'flex',gap:4,flexWrap:'wrap'}}>
                            {c.signals_critical > 0 && (
                              <span className="badge badge-today">{c.signals_critical} крит.</span>
                            )}
                            {c.signals_warning > 0 && (
                              <span className="badge badge-warn">{c.signals_warning} важн.</span>
                            )}
                            {c.top_signal && (
                              <div style={{fontSize:10,color:'var(--text3)',marginTop:2}}>
                                {SIGNAL_LABELS[c.top_signal.type]||c.top_signal.type}
                              </div>
                            )}
                          </div>
                        ) : (
                          <span style={{color:'var(--text3)',fontSize:11}}>—</span>
                        )}
                      </td>
                      <td style={{fontSize:12,color:'var(--accent)'}}>{isExpanded ? '▲' : '▼'}</td>
                    </tr>
                    {isExpanded && (
                      <CampaignDrillDown
                        campaign={c}
                        accountId={accountId}
                        dateFrom={activeDateFrom}
                        dateTo={activeDateTo}
                      />
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        ) : (
          <table>
            <thead>
              <tr>
                <th style={{minWidth:220}}>Фраза</th>
                <th>Ставка</th>
                {COLS.map(c=>(
                  <th key={c.key} style={{cursor:'pointer',whiteSpace:'nowrap'}}
                    onClick={()=>toggleSort(c.key)}>
                    {c.label}{sortBy===c.key?(sortDir>0?' ↑':' ↓'):''}
                  </th>
                ))}
                <th>Δ клики</th>
                <th>Сигнал</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0,300).map(kw=>(
                <tr key={kw.id} style={kw.signal?{background:'rgba(255,79,79,0.03)'}:{}}>
                  <td style={{fontFamily:'monospace',fontSize:11,maxWidth:260}}>
                    <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{kw.phrase}</div>
                    {kw.status && kw.status!=='ACTIVE' && <span style={{fontSize:10,color:'var(--text3)'}}>· {kw.status}</span>}
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
                    {kw.signal && (
                      <span className={`badge ${kw.signal.severity==='critical'?'badge-today':'badge-warn'}`} style={{fontSize:10}}>
                        {SIGNAL_LABELS[kw.signal.type]||kw.signal.type}
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
    </>
  )
}

// ═══════════════════════════════════════════════════════════════════════
// Ставки (бывш. bids.js)
// ═══════════════════════════════════════════════════════════════════════

function BidsSection({ accountId }) {
  const PERIODS = [
    {key:'yesterday',label:'Вчера'},
    {key:'3d',label:'3 дня'},
    {key:'week',label:'Неделя'},
    {key:'month',label:'Месяц'},
  ]

  function fR(n)  { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }
  function fP(n)  { return (!n||n===0)?'—':(Math.round(n*10)/10) }
  function fN(n)  { return (n==null||n==='')?'—':Math.round(n).toLocaleString('ru') }
  function fPct(n){ return n==null?'—':(Math.round(n*10)/10)+'%' }

  const SIGNAL_META = {
    low_position:       { label:'📍 Низкая позиция',    color:'var(--red)' },
    traffic_drop:       { label:'📉 Падение трафика',   color:'var(--red)' },
    epk_bid_collapse:   { label:'⚠️ ЕПК-обвал ставок',  color:'var(--red)' },
    spend_no_conversion:{ label:'💸 Расход без конверсий',color:'var(--red)' },
    cpc_spike:          { label:'💰 Рост CPC',           color:'var(--yellow)' },
    zero_ctr:           { label:'👁 CTR = 0',            color:'var(--yellow)' },
    low_ctr:            { label:'📊 Низкий CTR',         color:'var(--yellow)' },
    click_position_gap: { label:'⬇ Разрыв позиций',     color:'var(--yellow)' },
    high_bounce_rate:   { label:'↩ Высокий bounce',     color:'var(--yellow)' },
    scale_opportunity:  { label:'📈 Точка роста',        color:'var(--green)' },
  }

  function posColor(pos) {
    if (!pos) return 'inherit'
    if (pos > 4) return 'var(--red)'
    if (pos > 3) return '#e07b00'
    if (pos < 2) return 'var(--green)'
    return 'inherit'
  }
  function brColor(br) {
    if (!br) return 'inherit'
    if (br > 75) return 'var(--red)'
    if (br > 60) return '#e07b00'
    if (br < 40) return 'var(--green)'
    return 'inherit'
  }

  const [period, setPeriod]           = useState('week')
  const [keywords, setKeywords]       = useState([])
  const [campaigns, setCampaigns]     = useState([])
  const [adGroups, setAdGroups]       = useState([])
  const [loading, setLoading]         = useState(false)
  const [search, setSearch]           = useState('')
  const [selCampaign, setSelCampaign] = useState('')
  const [selGroup, setSelGroup]       = useState('')
  const [hideAuto, setHideAuto]       = useState(true)
  const [onlySignals, setOnlySignals] = useState(false)
  const [cpl, setCpl]                 = useState(2000)
  const [cr, setCr]                   = useState(5)
  const [sortKey, setSortKey]         = useState('signal')

  useEffect(() => {
    if (!accountId) return
    api.getCampaigns(accountId, period).then(c => setCampaigns(c||[])).catch(console.error)
  }, [accountId, period])

  useEffect(() => {
    if (!accountId || !selCampaign) { setAdGroups([]); setSelGroup(''); return }
    api.getAdGroups(accountId, selCampaign).then(g => setAdGroups(g||[])).catch(() => setAdGroups([]))
  }, [accountId, selCampaign])

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    let params = `?period=${period}&limit=500&active_only=true`
    if (selCampaign) params += `&campaign_id=${selCampaign}`
    if (selGroup)    params += `&ad_group_id=${selGroup}`
    if (search)      params += `&search=${encodeURIComponent(search)}`
    api.getKeywords(accountId, params)
      .then(k => setKeywords(Array.isArray(k) ? k : []))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [accountId, period, selCampaign, selGroup, search])

  const filtered = keywords
    .filter(kw => {
      if (hideAuto && kw.phrase?.includes('---autotargeting')) return false
      if (onlySignals && !kw.signal) return false
      return true
    })
    .sort((a, b) => {
      if (sortKey === 'signal') {
        const aS = a.signal ? 0 : 1
        const bS = b.signal ? 0 : 1
        if (aS !== bS) return aS - bS
        return (b.spend||0) - (a.spend||0)
      }
      if (sortKey === 'spend')    return (b.spend||0) - (a.spend||0)
      if (sortKey === 'position') return (a.avg_position||99) - (b.avg_position||99)
      if (sortKey === 'clicks')   return (b.clicks||0) - (a.clicks||0)
      if (sortKey === 'bid_diff') {
        const da = a.recommended_bid && a.current_bid ? a.recommended_bid - a.current_bid : 0
        const db = b.recommended_bid && b.current_bid ? b.recommended_bid - b.current_bid : 0
        return Math.abs(db) - Math.abs(da)
      }
      return 0
    })

  const calcBid = () => Math.round(cpl * cr / 100)

  const signalCounts = Object.entries(SIGNAL_META).map(([key, meta]) => ({
    key, ...meta,
    count: keywords.filter(kw => kw.signal?.type === key).length,
  })).filter(r => r.count > 0)

  const manualCount = campaigns.filter(c => c.strategy_type === 'MANUAL_CPC').length

  const totals = filtered.reduce((acc, kw) => {
    acc.clicks   += kw.clicks || 0
    acc.spend    += kw.spend  || 0
    acc.impressions += kw.impressions || 0
    return acc
  }, { clicks: 0, spend: 0, impressions: 0 })
  const totalCpc = totals.clicks > 0 ? Math.round(totals.spend / totals.clicks) : 0
  const totalCtr = totals.impressions > 0 ? (totals.clicks / totals.impressions * 100).toFixed(1) : 0

  return (
    <>
      <div style={{display:'flex',gap:8,marginBottom:12,flexWrap:'wrap',alignItems:'center'}}>
        <div className="period-tabs">
          {PERIODS.map(p=>(
            <div key={p.key} className={`period-tab${period===p.key?' active':''}`}
              onClick={()=>setPeriod(p.key)}>{p.label}</div>
          ))}
        </div>
        <input
          placeholder="Поиск по фразе..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{width:180}}
        />
        <select
          value={selCampaign}
          onChange={e => { setSelCampaign(e.target.value); setSelGroup('') }}
          className="btn"
          style={{padding:'5px 10px',maxWidth:260}}
        >
          <option value="">Все кампании ({campaigns.length})</option>
          {campaigns.map(c => (
            <option key={c.id} value={c.id}>
              {c.strategy_type==='MANUAL_CPC'?'✎ ':'⚙ '}{c.name}
            </option>
          ))}
        </select>

        {selCampaign && adGroups.length > 0 && (
          <select value={selGroup} onChange={e=>setSelGroup(e.target.value)}
            className="btn" style={{padding:'5px 10px',maxWidth:220}}>
            <option value="">Все группы ({adGroups.length})</option>
            {adGroups.map(g=>(
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
        )}

        <select value={sortKey} onChange={e=>setSortKey(e.target.value)}
          className="btn" style={{padding:'5px 10px'}}>
          <option value="signal">По сигналам</option>
          <option value="spend">По расходу</option>
          <option value="position">По позиции</option>
          <option value="clicks">По кликам</option>
          <option value="bid_diff">По откл. ставки</option>
        </select>

        <button className={`btn${hideAuto?' btn-primary':''}`}
          onClick={()=>setHideAuto(h=>!h)}>
          {hideAuto?'✓ ':''}Скрыть автотаргетинг
        </button>
        <button className={`btn${onlySignals?' btn-primary':''}`}
          onClick={()=>setOnlySignals(p=>!p)}>
          {onlySignals?'✓ ':''}Только с сигналами
        </button>
        <span style={{fontSize:11,color:'var(--text3)'}}>
          {filtered.length} ключей
          {manualCount>0 && (
            <span style={{marginLeft:6,color:'var(--green)'}}>✎ {manualCount} ручных РК</span>
          )}
        </span>
      </div>

      {filtered.length > 0 && (
        <div style={{display:'flex',gap:16,marginBottom:12,fontSize:12,color:'var(--text2)'}}>
          <span>Клики: <b>{totals.clicks.toLocaleString('ru')}</b></span>
          <span>Расход: <b>{Math.round(totals.spend).toLocaleString('ru')} ₽</b></span>
          <span>CTR: <b>{totalCtr}%</b></span>
          <span>CPC: <b>{totalCpc} ₽</b></span>
        </div>
      )}

      <div style={{display:'grid',gridTemplateColumns:'1fr 300px',gap:14}}>

        <div className="card" style={{padding:0,overflow:'auto'}}>
          {loading ? (
            <div style={{padding:'2rem',textAlign:'center',color:'var(--text3)'}}>Загрузка...</div>
          ) : filtered.length === 0 ? (
            <div style={{padding:'2rem',textAlign:'center',color:'var(--text3)'}}>
              {keywords.length===0 ? 'Нет данных — запустите сбор данных' : 'Нет ключей по фильтру'}
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{minWidth:200}}>Фраза</th>
                  <th title="Текущая ставка из кабинета">Ставка</th>
                  <th title="Рекомендованная ставка">Рек. ставка</th>
                  <th title="AvgImpressionPosition — средняя позиция показа">Поз. пок.</th>
                  <th title="AvgClickPosition — средняя позиция клика">Поз. кл.</th>
                  <th title="AvgTrafficVolume 0–150 — доступный объём трафика в системе">Объём</th>
                  <th title="Клики за период">Клики</th>
                  <th title="Дельта кликов к предыдущему периоду">Δ кл.</th>
                  <th title="Click-through rate">CTR</th>
                  <th title="Средняя цена клика">CPC</th>
                  <th title="BounceRate — доля отказов по кликам">Bounce</th>
                  <th title="Расход за период">Расход</th>
                  <th>Сигнал</th>
                </tr>
              </thead>
              <tbody>
                {filtered.slice(0, 400).map(kw => {
                  const sig     = kw.signal
                  const sigMeta = sig ? SIGNAL_META[sig.type] : null
                  const recBid  = kw.recommended_bid
                  const diff    = recBid && kw.current_bid ? recBid - kw.current_bid : null
                  const diffPct = diff && kw.current_bid ? Math.round(diff/kw.current_bid*100) : null
                  const rowBg   = sig
                    ? sig.severity==='critical' ? 'rgba(255,79,79,0.05)' : 'rgba(255,185,0,0.04)'
                    : {}
                  return (
                    <tr key={kw.id} style={{background: rowBg}}>
                      <td style={{fontFamily:'monospace',fontSize:11,maxWidth:240}}>
                        <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}
                          title={kw.phrase}>{kw.phrase}</div>
                      </td>

                      <td style={{whiteSpace:'nowrap'}}>
                        {kw.current_bid ? fR(kw.current_bid) : '—'}
                        {kw.bid_delta != null && (
                          <div style={{fontSize:9,color:kw.bid_delta>0?'var(--green)':'var(--red)'}}>
                            {kw.bid_delta>0?'▲':'▼'}{Math.abs(kw.bid_delta)}%
                          </div>
                        )}
                      </td>

                      <td style={{whiteSpace:'nowrap'}}>
                        {recBid ? (
                          <span style={{fontWeight:600,color:diff>0?'var(--green)':'var(--red)'}}>
                            {fR(recBid)}
                            {diffPct != null && (
                              <span style={{fontSize:9,marginLeft:3}}>
                                {diff>0?'▲':'▼'}{Math.abs(diffPct)}%
                              </span>
                            )}
                          </span>
                        ) : '—'}
                      </td>

                      <td>
                        <span style={{color:posColor(kw.avg_position),fontWeight:kw.avg_position>3?600:400}}>
                          {fP(kw.avg_position)}
                        </span>
                        {kw.position_delta != null && (
                          <div style={{fontSize:9,color:kw.position_delta>0?'var(--green)':'var(--red)'}}>
                            {kw.position_delta>0?'▲':'▼'}{Math.abs(kw.position_delta)}%
                          </div>
                        )}
                      </td>

                      <td>
                        {fP(kw.avg_click_position)}
                        {kw.click_position_gap != null && kw.click_position_gap > 1.5 && (
                          <div style={{fontSize:9,color:'var(--yellow)'}}>gap {kw.click_position_gap}</div>
                        )}
                      </td>

                      <td style={{color:kw.traffic_volume>100?'var(--green)':kw.traffic_volume>50?'inherit':'var(--text3)'}}>
                        {fN(kw.traffic_volume)}
                      </td>

                      <td>{fN(kw.clicks)}</td>

                      <td>
                        {kw.click_delta != null ? (
                          <span style={{fontSize:11,color:kw.click_delta>0?'var(--green)':'var(--red)'}}>
                            {kw.click_delta>0?'▲':'▼'}{Math.abs(kw.click_delta)}%
                          </span>
                        ) : '—'}
                      </td>

                      <td>
                        <span style={{
                          color: kw.ctr>5?'var(--green)':kw.ctr>2?'inherit':kw.ctr>0?'#e07b00':'var(--red)',
                        }}>
                          {kw.ctr != null ? fPct(kw.ctr) : '—'}
                        </span>
                        {kw.weighted_ctr != null && kw.weighted_ctr !== kw.ctr && (
                          <div style={{fontSize:9,color:'var(--text3)'}}>взв. {fPct(kw.weighted_ctr)}</div>
                        )}
                      </td>

                      <td>{kw.avg_cpc != null ? fR(kw.avg_cpc) : '—'}</td>

                      <td>
                        <span style={{color:brColor(kw.bounce_rate)}}>
                          {kw.bounce_rate != null ? fPct(kw.bounce_rate) : '—'}
                        </span>
                      </td>

                      <td>{fR(kw.spend)}</td>

                      <td style={{maxWidth:160}}>
                        {sig && sigMeta && (
                          <div style={{fontSize:10,lineHeight:1.4}}>
                            <div style={{color:sigMeta.color,fontWeight:600,marginBottom:2}}>
                              {sigMeta.label}
                            </div>
                            <div style={{color:'var(--text3)',fontSize:9,overflow:'hidden',
                              textOverflow:'ellipsis',whiteSpace:'nowrap',maxWidth:150}}
                              title={sig.action}>
                              {sig.action?.slice(0,60)}
                            </div>
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
                {filtered.length > 400 && (
                  <tr>
                    <td colSpan={13} style={{textAlign:'center',color:'var(--text3)',
                      padding:'1rem',fontSize:12}}>
                      Показано 400 из {filtered.length}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        <div style={{display:'flex',flexDirection:'column',gap:12}}>

          <div className="card">
            <div className="card-title">Калькулятор ставки</div>
            <div style={{display:'flex',flexDirection:'column',gap:10}}>
              <label style={{display:'flex',flexDirection:'column',gap:4}}>
                <span style={{fontSize:11,color:'var(--text3)'}}>Целевой CPL, ₽</span>
                <input type="number" value={cpl} onChange={e=>setCpl(Number(e.target.value))} />
              </label>
              <label style={{display:'flex',flexDirection:'column',gap:4}}>
                <span style={{fontSize:11,color:'var(--text3)'}}>Ожидаемый CR, %</span>
                <input type="number" value={cr} onChange={e=>setCr(Number(e.target.value))} />
              </label>
              <div style={{background:'var(--bg4)',borderRadius:8,padding:'12px'}}>
                <div style={{fontSize:11,color:'var(--text3)',marginBottom:4}}>Рекомендуемая ставка</div>
                <div style={{fontSize:22,fontWeight:700,color:'var(--accent)'}}>{fR(calcBid())}</div>
                <div style={{fontSize:10,color:'var(--text3)',marginTop:3}}>
                  CPL {cpl}₽ × CR {cr}% = {calcBid()}₽
                </div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-title">Активные сигналы</div>
            {signalCounts.length === 0 ? (
              <div style={{fontSize:12,color:'var(--text3)'}}>Сигналов нет ✓</div>
            ) : signalCounts.map(r => (
              <div key={r.key} style={{
                display:'flex',justifyContent:'space-between',alignItems:'center',
                padding:'5px 0',borderBottom:'1px solid var(--border)',
              }}>
                <span style={{fontSize:12,color:r.color}}>{r.label}</span>
                <span style={{fontWeight:600,fontSize:13}}>{r.count}</span>
              </div>
            ))}
          </div>

          <div className="card" style={{fontSize:11,color:'var(--text2)'}}>
            <div style={{fontWeight:500,marginBottom:6}}>Условные обозначения</div>
            <div style={{lineHeight:2,color:'var(--text3)'}}>
              <div><span style={{color:'var(--red)'}}>●</span> Критичный сигнал</div>
              <div><span style={{color:'var(--yellow)'}}>●</span> Предупреждение</div>
              <div><span style={{color:'var(--green)'}}>●</span> Точка роста</div>
              <div>Объём &gt;100 = много трафика</div>
              <div>Позиция &lt;2 = топ ✓</div>
              <div>Bounce &gt;60% = проблема</div>
              <div>CTR &lt;1% при поз.&lt;3 = плохое объявление</div>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

// ═══════════════════════════════════════════════════════════════════════
// Корректировки (бывш. adjustments.js)
// ═══════════════════════════════════════════════════════════════════════

function AdjustmentsSection({ accountId }) {
  function fP(n){return (!n&&n!==0)?'—':(Math.round(n*10)/10)+'%'}
  function fN(n){return (!n&&n!==0)?'—':Math.round(n)}
  function fS(n){return !n?'—':Math.round(n)+'с'}

  const TABS=['Устройства','Регионы','Время','Дни недели']
  const DAYS=['','Пн','Вт','Ср','Чт','Пт','Сб','Вс']

  function EmptyMsg({msg='Данные появятся после следующего сбора'}) {
    return <div style={{padding:'2rem',textAlign:'center',color:'var(--text3)',fontSize:13}}>{msg}</div>
  }

  function RecommendBadge({bounce, visits}) {
    if (!bounce) return <span style={{color:'var(--text3)'}}>—</span>
    if (bounce > 70 && visits > 100) return <span className="badge badge-bad">-50% корректировка</span>
    if (bounce > 55 && visits > 50)  return <span className="badge badge-warn">-30% корректировка</span>
    if (bounce < 20 && visits > 30)  return <span className="badge badge-ok">+20% корректировка</span>
    return <span style={{color:'var(--text3)'}}>—</span>
  }

  const [tab, setTab] = useState('Устройства')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    api.getMetrikaSnapshot(accountId)
      .then(d => setData(d?.data || null))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [accountId])

  function renderDevices() {
    const rows = data?.devices || []
    if (!rows.length) return <EmptyMsg msg="Устройства не собраны. Нажмите «Обновить данные»." />
    return (
      <table>
        <thead><tr><th>Устройство</th><th>Визиты</th><th>Отказы</th><th>Время</th><th>Рекомендация</th></tr></thead>
        <tbody>
          {rows.map((r,i) => (
            <tr key={i}>
              <td style={{fontWeight:500}}>
                {r.deviceCategory==='desktop'?'🖥 Десктоп':r.deviceCategory==='mobile'?'📱 Мобильный':r.deviceCategory==='tablet'?'📟 Планшет':r.deviceCategory||'—'}
              </td>
              <td>{fN(r.visits)}</td>
              <td><span style={{color:(r.bounceRate||0)>60?'var(--red)':(r.bounceRate||0)>40?'var(--yellow)':'var(--green)'}}>{fP(r.bounceRate)}</span></td>
              <td>{fS(r.avgVisitDurationSeconds)}</td>
              <td><RecommendBadge bounce={r.bounceRate} visits={r.visits} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  function renderRegions() {
    const rows = data?.regions || []
    if (!rows.length) return <EmptyMsg msg="Регионы не собраны. Нажмите «Обновить данные»." />
    return (
      <table>
        <thead><tr><th>Город</th><th>Визиты</th><th>Отказы</th><th>Время</th><th>Рекомендация</th></tr></thead>
        <tbody>
          {rows.slice(0,30).map((r,i) => (
            <tr key={i}>
              <td style={{fontWeight:500}}>{r.regionCity||'—'}</td>
              <td>{fN(r.visits)}</td>
              <td style={{color:(r.bounceRate||0)>60?'var(--red)':'inherit'}}>{fP(r.bounceRate)}</td>
              <td>{fS(r.avgVisitDurationSeconds)}</td>
              <td><RecommendBadge bounce={r.bounceRate} visits={r.visits} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  function renderTime() {
    const rows = data?.by_hour || []
    if (!rows.length) return <EmptyMsg msg="Данные по часам не собраны." />
    const maxV = Math.max(...rows.map(r=>r.visits||0), 1)
    return (
      <div style={{padding:16}}>
        <div style={{fontSize:12,color:'var(--text3)',marginBottom:8}}>Визиты по часам суток (МСК)</div>
        <div style={{display:'flex',alignItems:'flex-end',gap:2,height:80,marginBottom:6}}>
          {Array.from({length:24},(_,h) => {
            const row = rows.find(r=>Number(r.hourOfDay)===h)
            const v = row?.visits||0
            const height = Math.max(2,(v/maxV)*70)
            const isLow = v < maxV * 0.1
            return (
              <div key={h} style={{flex:1,display:'flex',flexDirection:'column',alignItems:'center'}}>
                <div style={{width:'80%',height,background:isLow?'var(--border2)':'var(--accent)',borderRadius:2,opacity:isLow?0.4:0.8}} />
              </div>
            )
          })}
        </div>
        <div style={{display:'flex',fontSize:9,color:'var(--text3)'}}>
          {[0,6,12,18].map(h=>(
            <div key={h} style={{flex:6,textAlign:'center'}}>{h}:00</div>
          ))}
          <div style={{flex:6,textAlign:'center'}}>24</div>
        </div>
        <table style={{marginTop:14}}>
          <thead><tr><th>Час</th><th>Визиты</th><th>Отказы</th></tr></thead>
          <tbody>
            {[...rows].sort((a,b)=>(b.visits||0)-(a.visits||0)).slice(0,10).map((r,i)=>(
              <tr key={i}><td>{r.hourOfDay}:00</td><td>{fN(r.visits)}</td><td>{fP(r.bounceRate)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  function renderWeekdays() {
    const rows = data?.by_weekday || []
    if (!rows.length) return <EmptyMsg />
    const maxV = Math.max(...rows.map(r=>r.visits||0), 1)
    const sorted = [...rows].sort((a,b)=>Number(a.dayOfWeek)-Number(b.dayOfWeek))
    return (
      <div style={{padding:16}}>
        <div style={{display:'flex',gap:8,marginBottom:8}}>
          {sorted.map((r,i)=>{
            const v = r.visits||0
            const height = Math.max(20,(v/maxV)*100)
            return (
              <div key={i} style={{flex:1,textAlign:'center'}}>
                <div style={{display:'flex',alignItems:'flex-end',justifyContent:'center',height:100}}>
                  <div style={{width:'60%',height,background:'var(--accent)',borderRadius:4,opacity:0.8}} />
                </div>
                <div style={{fontSize:11,color:'var(--text2)',marginTop:4}}>{DAYS[r.dayOfWeek]||r.dayOfWeek}</div>
                <div style={{fontSize:12,fontWeight:600}}>{fN(v)}</div>
                <div style={{fontSize:10,color:(r.bounceRate||0)>60?'var(--red)':'var(--text3)'}}>{fP(r.bounceRate)}</div>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="period-tabs" style={{marginBottom:14,display:'inline-flex'}}>
        {TABS.map(t=>(
          <div key={t} className={`period-tab${tab===t?' active':''}`} onClick={()=>setTab(t)}>{t}</div>
        ))}
      </div>

      {loading ? (
        <div style={{color:'var(--text3)',fontSize:13}}>Загрузка...</div>
      ) : !data ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-icon">⊕</div>
            <div className="empty-title">Нет данных из Метрики</div>
            <div className="empty-desc">Нажмите «Обновить данные» чтобы собрать статистику</div>
          </div>
        </div>
      ) : (
        <div className="card" style={{padding:0, overflow:'auto'}}>
          {tab==='Устройства' && renderDevices()}
          {tab==='Регионы' && renderRegions()}
          {tab==='Время' && renderTime()}
          {tab==='Дни недели' && renderWeekdays()}
        </div>
      )}
    </>
  )
}
