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

// Форматтеры
function fR(n)  { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }
function fP(n)  { return (!n||n===0)?'—':(Math.round(n*10)/10) }
function fN(n)  { return (n==null||n==='')?'—':Math.round(n).toLocaleString('ru') }
function fPct(n){ return n==null?'—':(Math.round(n*10)/10)+'%' }

// Сигналы по типу
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

// Цвет позиции
function posColor(pos) {
  if (!pos) return 'inherit'
  if (pos > 4) return 'var(--red)'
  if (pos > 3) return '#e07b00'
  if (pos < 2) return 'var(--green)'
  return 'inherit'
}
// Цвет bounce rate
function brColor(br) {
  if (!br) return 'inherit'
  if (br > 75) return 'var(--red)'
  if (br > 60) return '#e07b00'
  if (br < 40) return 'var(--green)'
  return 'inherit'
}

export default function Bids() {
  const { account, accounts, accountId, switchAccount } = useAccount()
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

  // Итоговые метрики по видимым ключам
  const totals = filtered.reduce((acc, kw) => {
    acc.clicks   += kw.clicks || 0
    acc.spend    += kw.spend  || 0
    acc.impressions += kw.impressions || 0
    return acc
  }, { clicks: 0, spend: 0, impressions: 0 })
  const totalCpc = totals.clicks > 0 ? Math.round(totals.spend / totals.clicks) : 0
  const totalCtr = totals.impressions > 0 ? (totals.clicks / totals.impressions * 100).toFixed(1) : 0

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Ставки</div>
        <div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
          <div className="period-tabs">
            {PERIODS.map(p=>(
              <div key={p.key} className={`period-tab${period===p.key?' active':''}`}
                onClick={()=>setPeriod(p.key)}>{p.label}</div>
            ))}
          </div>
        </div>
      </div>

      {/* Фильтры */}
      <div style={{display:'flex',gap:8,marginBottom:12,flexWrap:'wrap',alignItems:'center'}}>
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

      {/* Сводка по отфильтрованным */}
      {filtered.length > 0 && (
        <div style={{display:'flex',gap:16,marginBottom:12,fontSize:12,color:'var(--text2)'}}>
          <span>Клики: <b>{totals.clicks.toLocaleString('ru')}</b></span>
          <span>Расход: <b>{Math.round(totals.spend).toLocaleString('ru')} ₽</b></span>
          <span>CTR: <b>{totalCtr}%</b></span>
          <span>CPC: <b>{totalCpc} ₽</b></span>
        </div>
      )}

      <div style={{display:'grid',gridTemplateColumns:'1fr 300px',gap:14}}>

        {/* Таблица */}
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

                      {/* Текущая ставка */}
                      <td style={{whiteSpace:'nowrap'}}>
                        {kw.current_bid ? fR(kw.current_bid) : '—'}
                        {kw.bid_delta != null && (
                          <div style={{fontSize:9,color:kw.bid_delta>0?'var(--green)':'var(--red)'}}>
                            {kw.bid_delta>0?'▲':'▼'}{Math.abs(kw.bid_delta)}%
                          </div>
                        )}
                      </td>

                      {/* Рекомендованная ставка */}
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

                      {/* Позиция показа */}
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

                      {/* Позиция клика */}
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

                      {/* Δ кликов */}
                      <td>
                        {kw.click_delta != null ? (
                          <span style={{fontSize:11,color:kw.click_delta>0?'var(--green)':'var(--red)'}}>
                            {kw.click_delta>0?'▲':'▼'}{Math.abs(kw.click_delta)}%
                          </span>
                        ) : '—'}
                      </td>

                      {/* CTR с цветом */}
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

                      {/* Bounce rate */}
                      <td>
                        <span style={{color:brColor(kw.bounce_rate)}}>
                          {kw.bounce_rate != null ? fPct(kw.bounce_rate) : '—'}
                        </span>
                      </td>

                      <td>{fR(kw.spend)}</td>

                      {/* Сигнал */}
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

        {/* Правая панель */}
        <div style={{display:'flex',flexDirection:'column',gap:12}}>

          {/* Калькулятор */}
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

          {/* Сигналы */}
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

          {/* Легенда */}
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
    </Layout>
  )
}
