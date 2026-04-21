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

function fN(n)   { return n==null?'—':n>=1000?Math.round(n).toLocaleString('ru'):Math.round(n*10)/10 }
function fR(n)   { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }
function fP(n)   { return (!n||n===0)?'—':(Math.round(n*10)/10) }
function fPct(n) { return n==null?'—':(Math.round(n*10)/10)+'%' }

function Delta({ v, invert }) {
  if (v==null) return <span style={{color:'var(--text3)',fontSize:10}}>—</span>
  const up   = v > 0
  const good = invert ? !up : up
  return (
    <span style={{fontSize:10,color:good?'var(--green)':'var(--red)',marginLeft:4}}>
      {up?'▲':'▼'}{Math.abs(v)}%
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

export default function Campaigns() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [period, setPeriod]         = useState('week')
  const [view, setView]             = useState('campaigns')
  const [data, setData]             = useState([])
  const [campaigns, setCampaigns]   = useState([])
  const [loading, setLoading]       = useState(false)
  const [onlyActive, setOnlyActive] = useState(true)
  const [search, setSearch]         = useState('')
  const [selCampaign, setSelCampaign] = useState('')
  const [sortBy, setSortBy]         = useState('signals')
  const [sortDir, setSortDir]       = useState(-1)
  const [selected, setSelected]     = useState(null)

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    Promise.all([
      api.getCampaigns(accountId, period, onlyActive),
      view === 'keywords'
        ? api.getKeywords(accountId,
            `?period=${period}${onlyActive?'&active_only=true':''}` +
            `${selCampaign?'&campaign_id='+selCampaign:''}` +
            `${search?'&search='+encodeURIComponent(search):''}`)
        : Promise.resolve(null),
    ]).then(([camps, kws]) => {
      const list = onlyActive ? (camps||[]).filter(c=>c.is_active) : (camps||[])
      setCampaigns(list)
      if (view === 'campaigns') setData(list)
      else if (view === 'keywords' && kws) setData(kws)
    }).catch(console.error).finally(() => setLoading(false))
  }, [accountId, period, view, onlyActive, selCampaign, search])

  function toggleSort(key) {
    if (sortBy===key) setSortDir(d=>-d)
    else { setSortBy(key); setSortDir(-1) }
  }

  function sortVal(item, key) {
    if (key==='signals') return (item.signals_critical||0)*10 + (item.signals_warning||0)
    return item[key] || 0
  }

  const filtered = data
    .filter(item => !search || (item.name||item.phrase||'').toLowerCase().includes(search.toLowerCase()))
    .sort((a,b) => (sortVal(b,sortBy) - sortVal(a,sortBy)) * sortDir)

  const totals = filtered.reduce((acc,c) => {
    acc.spend   += c.spend  || 0
    acc.clicks  += c.clicks || 0
    acc.impressions += c.impressions || 0
    return acc
  }, {spend:0, clicks:0, impressions:0})

  const SortTh = ({k, label, title}) => (
    <th title={title} style={{cursor:'pointer',whiteSpace:'nowrap'}} onClick={()=>toggleSort(k)}>
      {label}{sortBy===k ? (sortDir>0?' ↑':' ↓') : ''}
    </th>
  )

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">По кампаниям</div>
        <div style={{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
          <div className="period-tabs">
            {PERIODS.map(p=>(
              <div key={p.key} className={`period-tab${period===p.key?' active':''}`}
                onClick={()=>setPeriod(p.key)}>{p.label}</div>
            ))}
          </div>
        </div>
      </div>

      {/* Вкладки */}
      <div style={{display:'flex',gap:4,marginBottom:12}}>
        {[
          {key:'campaigns',label:'Кампании'},
          {key:'keywords', label:'Ключевые слова'},
        ].map(v=>(
          <button key={v.key}
            className={`btn${view===v.key?' btn-primary':''}`}
            onClick={()=>setView(v.key)}
            style={{padding:'5px 14px',fontSize:12}}>
            {v.label}
          </button>
        ))}
      </div>

      {/* Фильтры */}
      <div style={{display:'flex',gap:8,marginBottom:12,flexWrap:'wrap',alignItems:'center'}}>
        <input placeholder="Поиск..." value={search}
          onChange={e=>setSearch(e.target.value)} style={{width:180}} />
        {view==='keywords' && (
          <select value={selCampaign}
            onChange={e=>setSelCampaign(e.target.value)}
            className="btn" style={{padding:'5px 10px',maxWidth:260}}>
            <option value="">Все кампании ({campaigns.length})</option>
            {campaigns.map(c=>(
              <option key={c.id} value={c.id}>
                {c.signals_critical>0?'🔴 ':c.signals_count>0?'🟡 ':''}{c.name}
              </option>
            ))}
          </select>
        )}
        <button className={`btn${onlyActive?' btn-primary':''}`}
          onClick={()=>setOnlyActive(v=>!v)}>
          {onlyActive?'✓ ':''}Только активные
        </button>
        <span style={{fontSize:11,color:'var(--text3)'}}>
          {filtered.length} {view==='campaigns'?'кампаний':'ключей'}
        </span>
      </div>

      {/* Сводка */}
      {view==='campaigns' && filtered.length>0 && (
        <div style={{display:'flex',gap:16,marginBottom:12,fontSize:12,color:'var(--text2)'}}>
          <span>Расход: <b>{Math.round(totals.spend).toLocaleString('ru')} ₽</b></span>
          <span>Клики: <b>{totals.clicks.toLocaleString('ru')}</b></span>
          <span>CTR: <b>{totals.impressions>0?(totals.clicks/totals.impressions*100).toFixed(1)+'%':'—'}</b></span>
          <span>CPC: <b>{totals.clicks>0?Math.round(totals.spend/totals.clicks)+' ₽':'—'}</b></span>
        </div>
      )}

      <div style={{display:'grid',gridTemplateColumns:selected?'1fr 360px':'1fr',gap:14}}>

        {/* Таблица кампаний */}
        {view==='campaigns' && (
          <div className="card" style={{padding:0,overflow:'auto'}}>
            <table>
              <thead>
                <tr>
                  <SortTh k="name"           label="Кампания" />
                  <th>Стратегия</th>
                  <SortTh k="signals"        label="Сигналы"    title="Количество активных сигналов" />
                  <SortTh k="spend"          label="Расход"     title="Расход за период" />
                  <SortTh k="clicks"         label="Клики" />
                  <th title="Дельта кликов">Δ кл.</th>
                  <SortTh k="ctr"            label="CTR" />
                  <SortTh k="avg_cpc"        label="CPC" />
                  <SortTh k="avg_position"   label="Поз."       title="Средняя позиция показа" />
                  <th title="Дельта позиции">Δ поз.</th>
                  <SortTh k="traffic_volume" label="Объём"      title="AvgTrafficVolume 0–150" />
                  <SortTh k="bounce_rate"    label="Bounce"     title="Bounce rate из Метрики" />
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={12} style={{textAlign:'center',color:'var(--text3)',padding:'2rem'}}>Загрузка...</td></tr>
                ) : filtered.length===0 ? (
                  <tr><td colSpan={12} style={{textAlign:'center',color:'var(--text3)',padding:'2rem'}}>
                    {data.length===0?'Нет данных':'Нет результатов'}
                  </td></tr>
                ) : filtered.map(c => {
                  const isSelected = selected?.id === c.id
                  const rowBg = c.signals_critical>0
                    ? 'rgba(255,79,79,0.04)'
                    : c.signals_count>0
                      ? 'rgba(255,185,0,0.03)'
                      : undefined
                  return (
                    <tr key={c.id}
                      onClick={()=>setSelected(isSelected?null:c)}
                      style={{cursor:'pointer',background:isSelected?'var(--bg4)':rowBg}}>

                      {/* Кампания */}
                      <td style={{maxWidth:260}}>
                        <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',
                          fontSize:12,fontWeight:500}} title={c.name}>{c.name}</div>
                        {c.has_epk_collapse && (
                          <div style={{fontSize:9,color:'var(--red)',marginTop:1}}>⚠️ ЕПК-обвал</div>
                        )}
                      </td>

                      {/* Стратегия */}
                      <td>
                        <span style={{fontSize:10,padding:'2px 6px',borderRadius:3,
                          background: c.strategy_type==='MANUAL_CPC'?'rgba(0,200,100,0.1)':'rgba(100,150,255,0.1)',
                          color: c.strategy_type==='MANUAL_CPC'?'var(--green)':'var(--accent)'}}>
                          {c.strategy_type==='MANUAL_CPC'?'✎ Ручные':'⚙ Авто'}
                        </span>
                      </td>

                      {/* Сигналы */}
                      <td>
                        {c.signals_critical>0 && (
                          <span style={{color:'var(--red)',fontWeight:600,fontSize:12,marginRight:4}}>
                            🔴{c.signals_critical}
                          </span>
                        )}
                        {c.signals_warning>0 && (
                          <span style={{color:'#e07b00',fontSize:12}}>
                            🟡{c.signals_warning}
                          </span>
                        )}
                        {c.signals_count===0 && (
                          <span style={{color:'var(--text3)',fontSize:10}}>—</span>
                        )}
                      </td>

                      <td>{fR(c.spend)}
                        {c.spend_delta!=null&&<Delta v={c.spend_delta} invert />}
                      </td>
                      <td>{fN(c.clicks)}</td>
                      <td><Delta v={c.click_delta} /></td>
                      <td>{fPct(c.ctr)}</td>
                      <td>{fR(c.avg_cpc)}</td>
                      <td><PosCell v={c.avg_position} /></td>
                      <td><Delta v={c.position_delta} /></td>
                      <td style={{
                        color: c.traffic_volume>100?'var(--green)':c.traffic_volume>50?'inherit':'var(--text3)',
                      }}>
                        {fN(c.traffic_volume)}
                      </td>
                      <td>
                        <span style={{
                          color: c.bounce_rate>75?'var(--red)':c.bounce_rate>60?'#e07b00':'inherit',
                        }}>
                          {c.bounce_rate!=null?fPct(c.bounce_rate):'—'}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Таблица ключей */}
        {view==='keywords' && (
          <div className="card" style={{padding:0,overflow:'auto'}}>
            <table>
              <thead>
                <tr>
                  <th style={{minWidth:200}}>Фраза</th>
                  <th>Клики</th><th>Δ</th>
                  <th title="Объём трафика">Объём</th>
                  <th>Поз.</th><th>CTR</th><th>CPC</th>
                  <th>Bounce</th><th>Расход</th>
                  <th>Сигнал</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={10} style={{textAlign:'center',color:'var(--text3)',padding:'2rem'}}>Загрузка...</td></tr>
                ) : filtered.map((kw,i) => {
                  const sig = kw.signal
                  return (
                    <tr key={i}
                      onClick={()=>setSelected(selected?.id===kw.id?null:kw)}
                      style={{
                        cursor:'pointer',
                        background: selected?.id===kw.id?'var(--bg4)'
                          : sig?.severity==='critical'?'rgba(255,79,79,0.04)'
                          : sig?'rgba(255,185,0,0.03)':undefined,
                      }}>
                      <td style={{fontFamily:'monospace',fontSize:11,maxWidth:220}}>
                        <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}
                          title={kw.phrase}>{kw.phrase}</div>
                      </td>
                      <td>{fN(kw.clicks)}</td>
                      <td><Delta v={kw.click_delta} /></td>
                      <td style={{color:kw.traffic_volume>100?'var(--green)':kw.traffic_volume>50?'inherit':'var(--text3)'}}>
                        {fN(kw.traffic_volume)}
                      </td>
                      <td><PosCell v={kw.avg_position} /></td>
                      <td>
                        <span style={{color:kw.ctr>5?'var(--green)':kw.ctr>2?'inherit':kw.ctr>0?'#e07b00':'var(--red)'}}>
                          {fPct(kw.ctr)}
                        </span>
                      </td>
                      <td>{kw.avg_cpc?fR(kw.avg_cpc):'—'}</td>
                      <td>
                        <span style={{color:kw.bounce_rate>75?'var(--red)':kw.bounce_rate>60?'#e07b00':'inherit'}}>
                          {kw.bounce_rate!=null?fPct(kw.bounce_rate):'—'}
                        </span>
                      </td>
                      <td>{fR(kw.spend)}</td>
                      <td>
                        {sig && (
                          <span style={{fontSize:10,color:
                            sig.severity==='critical'?'var(--red)':
                            sig.severity==='warning'?'#e07b00':'var(--green)'}}>
                            {SIGNAL_LABELS[sig.type]||sig.type}
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Боковая панель — детали кампании или ключа */}
        {selected && (
          <div style={{display:'flex',flexDirection:'column',gap:12}}>
            <div className="card">
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:12}}>
                <div className="card-title" style={{margin:0}}>
                  {view==='campaigns'?'Кампания':'Ключ'}
                </div>
                <button className="btn" onClick={()=>setSelected(null)} style={{padding:'2px 8px',fontSize:11}}>✕</button>
              </div>

              <div style={{fontWeight:500,fontSize:12,marginBottom:10,wordBreak:'break-word'}}>
                {selected.name || selected.phrase}
              </div>

              {/* Метрики */}
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginBottom:12}}>
                {view==='campaigns' ? [
                  {label:'Расход',       val: fR(selected.spend)},
                  {label:'Клики',        val: fN(selected.clicks)},
                  {label:'CTR',          val: fPct(selected.ctr)},
                  {label:'CPC',          val: fR(selected.avg_cpc)},
                  {label:'Поз. показа',  val: fP(selected.avg_position)},
                  {label:'Поз. клика',   val: fP(selected.avg_click_position)},
                  {label:'Объём тр.',    val: fN(selected.traffic_volume)},
                  {label:'Bounce rate',  val: fPct(selected.bounce_rate)},
                  {label:'Сессии',       val: fN(selected.sessions)},
                  {label:'Стратегия',    val: selected.strategy_type==='MANUAL_CPC'?'✎ Ручные':'⚙ Авто'},
                ] : [
                  {label:'Ставка',       val: fR(selected.current_bid)},
                  {label:'Рек. ставка',  val: fR(selected.recommended_bid)},
                  {label:'Клики',        val: fN(selected.clicks)},
                  {label:'CTR',          val: fPct(selected.ctr)},
                  {label:'CPC',          val: fR(selected.avg_cpc)},
                  {label:'Поз. показа',  val: fP(selected.avg_position)},
                  {label:'Поз. клика',   val: fP(selected.avg_click_position)},
                  {label:'Объём тр.',    val: fN(selected.traffic_volume)},
                  {label:'Bounce',       val: fPct(selected.bounce_rate)},
                  {label:'Расход',       val: fR(selected.spend)},
                ].map(({label,val}) => (
                  <div key={label} style={{background:'var(--bg4)',borderRadius:4,padding:'8px 10px'}}>
                    <div style={{fontSize:10,color:'var(--text3)',marginBottom:2}}>{label}</div>
                    <div style={{fontWeight:600,fontSize:13}}>{val}</div>
                  </div>
                ))}
              </div>

              {/* Топ-сигнал */}
              {(selected.top_signal || selected.signal) && (() => {
                const sig = selected.top_signal || selected.signal
                const color = sig.severity==='critical'?'var(--red)':sig.severity==='warning'?'#e07b00':'var(--green)'
                return (
                  <div style={{borderLeft:`3px solid ${color}`,paddingLeft:10}}>
                    <div style={{fontWeight:600,fontSize:12,color,marginBottom:4}}>
                      {SIGNAL_LABELS[sig.type]||sig.type}
                      {' · '}<span style={{fontWeight:400,color:'var(--text3)'}}>
                        {sig.priority==='today'?'🔴 Сегодня':sig.priority==='this_week'?'🟡 Неделя':''}
                      </span>
                    </div>
                    <div style={{fontSize:12,marginBottom:4,color:'var(--text2)'}}>
                      <b>Проблема:</b> {sig.description}
                    </div>
                    {sig.hypothesis && (
                      <div style={{fontSize:12,marginBottom:4,color:'var(--text2)'}}>
                        <b>Гипотеза:</b> {sig.hypothesis}
                      </div>
                    )}
                    <div style={{fontSize:12,marginBottom:4,color:'var(--text2)'}}>
                      <b>Действие:</b> {sig.action}
                    </div>
                    {sig.expected_outcome && (
                      <div style={{fontSize:11,color:'var(--text3)'}}>
                        Ожидаем: {sig.expected_outcome}
                      </div>
                    )}
                    {sig.calculation_logic && (
                      <div style={{fontSize:10,color:'var(--text3)',fontFamily:'monospace',marginTop:4}}>
                        {sig.calculation_logic}
                      </div>
                    )}
                  </div>
                )
              })()}

              {/* Все сигналы кампании */}
              {view==='campaigns' && selected.signals_count > 1 && (
                <div style={{marginTop:10,fontSize:11,color:'var(--text3)'}}>
                  Всего сигналов: {selected.signals_count}
                  {selected.signals_critical>0 && <span style={{color:'var(--red)'}}> · {selected.signals_critical} критичных</span>}
                  {selected.signals_warning>0 && <span style={{color:'#e07b00'}}> · {selected.signals_warning} предупреждений</span>}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </Layout>
  )
}
