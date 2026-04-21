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

function fR(n)   { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }
function fP(n)   { return (!n||n===0)?'—':(Math.round(n*10)/10) }
function fPct(n) { return n==null?'—':(Math.round(n*10)/10)+'%' }
function fN(n)   { return (n==null||n==='')?'—':Math.round(n).toLocaleString('ru') }

// Цвет для метрик
function signalColor(type) {
  const critical = ['low_position','traffic_drop','epk_bid_collapse','spend_no_conversion']
  const warning  = ['cpc_spike','zero_ctr','low_ctr','click_position_gap','high_bounce_rate','mobile_quality_issue']
  if (critical.includes(type)) return 'var(--red)'
  if (warning.includes(type))  return 'var(--yellow)'
  return 'var(--green)'
}

function signalLabel(type) {
  const labels = {
    low_position:        '📍 Низкая позиция',
    traffic_drop:        '📉 Падение трафика',
    epk_bid_collapse:    '⚠️ ЕПК-обвал',
    spend_no_conversion: '💸 Расход без конверсий',
    cpc_spike:           '💰 Рост CPC',
    zero_ctr:            '👁 CTR=0',
    low_ctr:             '📊 Низкий CTR',
    click_position_gap:  '⬇ Разрыв позиций',
    high_bounce_rate:    '↩ Высокий bounce',
    scale_opportunity:   '📈 Точка роста',
  }
  return labels[type] || type
}

export default function Keywords() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [period, setPeriod]     = useState('week')
  const [keywords, setKeywords] = useState([])
  const [loading, setLoading]   = useState(true)
  const [search, setSearch]     = useState('')
  const [sortKey, setSortKey]   = useState('signal')
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    api.getKeywords(accountId, `?limit=300&period=${period}`)
      .then(data => { setKeywords(Array.isArray(data)?data:[]); setLoading(false) })
      .catch(() => setLoading(false))
  }, [accountId, period])

  const filtered = keywords
    .filter(kw => !search || kw.phrase.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      if (sortKey === 'signal') {
        const aS = a.signal?.severity === 'critical' ? 0 : a.signal ? 1 : 2
        const bS = b.signal?.severity === 'critical' ? 0 : b.signal ? 1 : 2
        if (aS !== bS) return aS - bS
        return (b.spend||0) - (a.spend||0)
      }
      if (sortKey === 'clicks')   return (b.clicks||0)    - (a.clicks||0)
      if (sortKey === 'spend')    return (b.spend||0)     - (a.spend||0)
      if (sortKey === 'position') return (a.avg_position||99) - (b.avg_position||99)
      if (sortKey === 'traffic')  return (b.traffic_volume||0) - (a.traffic_volume||0)
      if (sortKey === 'bounce')   return (b.bounce_rate||0) - (a.bounce_rate||0)
      return 0
    })

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:'1rem'}}>
        <h1 style={{fontSize:20,fontWeight:500}}>Ключевые слова</h1>
        <div className="period-tabs">
          {PERIODS.map(p=>(
            <div key={p.key} className={`period-tab${period===p.key?' active':''}`}
              onClick={()=>setPeriod(p.key)}>{p.label}</div>
          ))}
        </div>
      </div>

      {/* Фильтры */}
      <div style={{display:'flex',gap:8,marginBottom:12,alignItems:'center',flexWrap:'wrap'}}>
        <input placeholder="Поиск по фразе..." value={search}
          onChange={e=>setSearch(e.target.value)} style={{width:220}} />
        <select value={sortKey} onChange={e=>setSortKey(e.target.value)}>
          <option value="signal">По сигналам</option>
          <option value="spend">По расходу</option>
          <option value="clicks">По кликам</option>
          <option value="position">По позиции</option>
          <option value="traffic">По объёму</option>
          <option value="bounce">По bounce rate</option>
        </select>
        <span style={{fontSize:12,color:'var(--text3)'}}>{filtered.length} ключей</span>
      </div>

      <div style={{display:'grid',gridTemplateColumns: selected?'1fr 360px':'1fr',gap:14}}>

        {/* Таблица */}
        <div className="card" style={{padding:0,overflow:'hidden'}}>
          <table>
            <thead>
              <tr>
                <th>Ключевая фраза</th>
                <th title="Клики за период">Клики</th>
                <th title="Дельта кликов">Δ</th>
                <th title="Объём трафика 0–150">Объём</th>
                <th title="Позиция показа">Поз.</th>
                <th title="CTR (Clicks/Impressions)">CTR</th>
                <th title="CPC средний">CPC</th>
                <th title="Bounce rate из Директа">Bounce</th>
                <th title="Расход">Расход</th>
                <th>Сигнал</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={10} style={{textAlign:'center',color:'var(--text3)',padding:'2rem'}}>
                  Загрузка...
                </td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={10} style={{textAlign:'center',color:'var(--text3)',padding:'2rem'}}>
                  {keywords.length===0 ? 'Нет данных' : 'Ничего не найдено'}
                </td></tr>
              ) : filtered.map(kw => {
                const sig = kw.signal
                const isSelected = selected?.id === kw.id
                return (
                  <tr key={kw.id}
                    onClick={() => setSelected(isSelected ? null : kw)}
                    style={{
                      cursor:'pointer',
                      background: isSelected
                        ? 'var(--bg4)'
                        : sig?.severity==='critical'
                          ? 'rgba(255,79,79,0.04)'
                          : sig
                            ? 'rgba(255,185,0,0.03)'
                            : undefined,
                    }}>

                    <td style={{fontFamily:'monospace',fontSize:11,maxWidth:220}}>
                      <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}
                        title={kw.phrase}>{kw.phrase}</div>
                    </td>

                    <td style={{textAlign:'right'}}>{fN(kw.clicks)}</td>

                    {/* Дельта кликов */}
                    <td style={{textAlign:'right',fontSize:10}}>
                      {kw.click_delta != null ? (
                        <span style={{color:kw.click_delta>0?'var(--green)':'var(--red)'}}>
                          {kw.click_delta>0?'▲':'▼'}{Math.abs(kw.click_delta)}%
                        </span>
                      ) : '—'}
                    </td>

                    {/* Объём трафика */}
                    <td style={{
                      textAlign:'right',
                      color: kw.traffic_volume>100?'var(--green)':kw.traffic_volume>50?'inherit':'var(--text3)',
                    }}>
                      {fN(kw.traffic_volume)}
                    </td>

                    {/* Позиция */}
                    <td style={{textAlign:'right'}}>
                      <span style={{
                        color: kw.avg_position>3?'var(--red)':kw.avg_position<2?'var(--green)':'inherit',
                        fontWeight: kw.avg_position>3?600:400,
                      }}>
                        {fP(kw.avg_position)}
                      </span>
                    </td>

                    {/* CTR */}
                    <td style={{textAlign:'right'}}>
                      <span style={{
                        color: kw.ctr>5?'var(--green)':kw.ctr>2?'inherit':kw.ctr>0?'#e07b00':'var(--red)',
                      }}>
                        {fPct(kw.ctr)}
                      </span>
                    </td>

                    <td style={{textAlign:'right'}}>{kw.avg_cpc != null ? fR(kw.avg_cpc) : '—'}</td>

                    {/* Bounce */}
                    <td style={{textAlign:'right'}}>
                      <span style={{
                        color: kw.bounce_rate>75?'var(--red)':kw.bounce_rate>60?'#e07b00':kw.bounce_rate&&kw.bounce_rate<40?'var(--green)':'inherit',
                      }}>
                        {kw.bounce_rate != null ? fPct(kw.bounce_rate) : '—'}
                      </span>
                    </td>

                    <td style={{textAlign:'right'}}>{fR(kw.spend)}</td>

                    {/* Сигнал */}
                    <td>
                      {sig && (
                        <span style={{
                          fontSize:10,fontWeight:500,
                          color: signalColor(sig.type),
                        }}>
                          {signalLabel(sig.type)}
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Боковая панель — детали ключа */}
        {selected && (
          <div style={{display:'flex',flexDirection:'column',gap:12}}>
            <div className="card">
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:12}}>
                <div className="card-title" style={{margin:0}}>Детали ключа</div>
                <button className="btn" onClick={()=>setSelected(null)} style={{padding:'2px 8px',fontSize:11}}>✕</button>
              </div>
              <div style={{fontFamily:'monospace',fontSize:11,wordBreak:'break-all',
                background:'var(--bg4)',padding:8,borderRadius:4,marginBottom:12}}>
                {selected.phrase}
              </div>

              {/* Метрики сеткой */}
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginBottom:12}}>
                {[
                  {label:'Ставка',       val: fR(selected.current_bid)},
                  {label:'Рек. ставка',  val: fR(selected.recommended_bid)},
                  {label:'Клики',        val: fN(selected.clicks)},
                  {label:'Показы',       val: fN(selected.impressions)},
                  {label:'CTR',          val: fPct(selected.ctr)},
                  {label:'Вз. CTR',      val: fPct(selected.weighted_ctr)},
                  {label:'CPC',          val: fR(selected.avg_cpc)},
                  {label:'Расход',       val: fR(selected.spend)},
                  {label:'Поз. показа',  val: fP(selected.avg_position)},
                  {label:'Поз. клика',   val: fP(selected.avg_click_position)},
                  {label:'Объём тр.',    val: fN(selected.traffic_volume)},
                  {label:'Bounce rate',  val: fPct(selected.bounce_rate)},
                  {label:'Сессии',       val: fN(selected.sessions)},
                  {label:'Разрыв поз.',  val: fP(selected.click_position_gap)},
                ].map(({label,val}) => (
                  <div key={label} style={{background:'var(--bg4)',borderRadius:4,padding:'8px 10px'}}>
                    <div style={{fontSize:10,color:'var(--text3)',marginBottom:2}}>{label}</div>
                    <div style={{fontWeight:600,fontSize:13}}>{val}</div>
                  </div>
                ))}
              </div>

              {/* Сигнал */}
              {selected.signal && (
                <div style={{
                  borderLeft:`3px solid ${signalColor(selected.signal.type)}`,
                  paddingLeft:10,
                  marginBottom:10,
                }}>
                  <div style={{fontWeight:600,fontSize:12,color:signalColor(selected.signal.type),marginBottom:4}}>
                    {signalLabel(selected.signal.type)}
                    {' · '}<span style={{fontWeight:400,color:'var(--text3)'}}>{selected.signal.priority}</span>
                  </div>
                  <div style={{fontSize:12,marginBottom:6,color:'var(--text2)'}}>
                    <b>Проблема:</b> {selected.signal.description}
                  </div>
                  <div style={{fontSize:12,marginBottom:6,color:'var(--text2)'}}>
                    <b>Гипотеза:</b> {selected.signal.hypothesis}
                  </div>
                  <div style={{fontSize:12,marginBottom:6,color:'var(--text2)'}}>
                    <b>Действие:</b> {selected.signal.action}
                  </div>
                  {selected.signal.expected_outcome && (
                    <div style={{fontSize:11,color:'var(--text3)'}}>
                      Ожидаем: {selected.signal.expected_outcome}
                    </div>
                  )}
                  {selected.signal.calculation_logic && (
                    <div style={{fontSize:10,color:'var(--text3)',fontFamily:'monospace',marginTop:4}}>
                      {selected.signal.calculation_logic}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </Layout>
  )
}
