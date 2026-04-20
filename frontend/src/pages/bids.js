import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const PERIODS = [{key:'yesterday',label:'Вчера'},{key:'3d',label:'3 дня'},{key:'week',label:'Неделя'},{key:'month',label:'Месяц'}]

function fR(n) { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }
function fP(n) { return (!n||n===0)?'—':(Math.round(n*10)/10) }
function fN(n) { return (n==null||n==='')?'—':Math.round(n) }

const SIGNAL_RULES = [
  { key:'low_position',       label:'📍 Низкая позиция',   color:'var(--red)' },
  { key:'traffic_drop',       label:'📉 Падение трафика',  color:'var(--red)' },
  { key:'zero_ctr',           label:'👁 CTR = 0',           color:'var(--yellow)' },
  { key:'click_position_gap', label:'⬇ Поз. клика хуже',  color:'var(--yellow)' },
  { key:'low_ctr',            label:'📊 Низкий CTR',       color:'var(--yellow)' },
]

export default function Bids() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [period, setPeriod] = useState('week')
  const [keywords, setKeywords] = useState([])
  const [campaigns, setCampaigns] = useState([])
  const [adGroups, setAdGroups] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [selCampaign, setSelCampaign] = useState('')
  const [selGroup, setSelGroup] = useState('')
  const [hideAuto, setHideAuto] = useState(true)
  const [onlyProblems, setOnlyProblems] = useState(false)
  const [cpl, setCpl] = useState(2000)
  const [cr, setCr] = useState(5)

  // Загрузка всех кампаний (без фильтра по стратегии)
  useEffect(() => {
    if (!accountId) return
    api.getCampaigns(accountId, period)
      .then(c => setCampaigns(c || []))
      .catch(console.error)
  }, [accountId, period])

  // Загрузка групп при выборе кампании
  useEffect(() => {
    if (!accountId || !selCampaign) {
      setAdGroups([])
      setSelGroup('')
      return
    }
    api.getAdGroups(accountId, selCampaign)
      .then(g => setAdGroups(g || []))
      .catch(() => setAdGroups([]))
  }, [accountId, selCampaign])

  // Загрузка ключей
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

  const filtered = keywords.filter(kw => {
    if (hideAuto && kw.phrase?.includes('---autotargeting')) return false
    if (onlyProblems && !kw.problem) return false
    return true
  })

  const calcBid = () => Math.round(cpl * cr / 100)

  const problemCounts = SIGNAL_RULES.map(r => ({
    ...r,
    count: keywords.filter(kw => kw.problem?.type === r.key).length
  })).filter(r => r.count > 0)

  const manualCount = campaigns.filter(c => c.strategy_type === 'MANUAL_CPC').length

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
          style={{padding:'5px 10px', maxWidth:260}}
        >
          <option value="">Все кампании ({campaigns.length})</option>
          {campaigns.map(c => (
            <option key={c.id} value={c.id}>
              {c.strategy_type === 'MANUAL_CPC' ? '✎ ' : '⚙ '}{c.name}
            </option>
          ))}
        </select>

        {selCampaign && adGroups.length > 0 && (
          <select
            value={selGroup}
            onChange={e => setSelGroup(e.target.value)}
            className="btn"
            style={{padding:'5px 10px', maxWidth:220}}
          >
            <option value="">Все группы ({adGroups.length})</option>
            {adGroups.map(g => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
        )}

        <button className={`btn${hideAuto?' btn-primary':''}`}
          onClick={() => setHideAuto(h => !h)}>
          {hideAuto ? '✓ ' : ''}Скрыть автотаргетинг
        </button>
        <button className={`btn${onlyProblems?' btn-primary':''}`}
          onClick={() => setOnlyProblems(p => !p)}>
          {onlyProblems ? '✓ ' : ''}Только с сигналами
        </button>
        <span style={{fontSize:11,color:'var(--text3)'}}>
          {filtered.length} ключей
          {manualCount > 0 && (
            <span style={{marginLeft:6,color:'var(--green)'}}>✎ {manualCount} ручных РК</span>
          )}
        </span>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'1fr 290px',gap:14}}>
        {/* Таблица */}
        <div className="card" style={{padding:0,overflow:'auto'}}>
          {loading ? (
            <div style={{padding:'2rem',textAlign:'center',color:'var(--text3)'}}>Загрузка...</div>
          ) : filtered.length === 0 ? (
            <div style={{padding:'2rem',textAlign:'center',color:'var(--text3)'}}>
              {keywords.length === 0 ? 'Нет данных — запустите сбор данных' : 'Нет ключей по фильтру'}
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{minWidth:200}}>Фраза</th>
                  <th>Тек. ставка</th>
                  <th>Рек. ставка</th>
                  <th>Поз. показа</th>
                  <th>Поз. клика</th>
                  <th>Объём тр.</th>
                  <th>Клики</th>
                  <th>Δ клики</th>
                  <th>CTR</th>
                  <th>CPC</th>
                  <th>Расход</th>
                  <th>Сигнал</th>
                </tr>
              </thead>
              <tbody>
                {filtered.slice(0, 300).map(kw => {
                  const hasProblem = !!kw.problem
                  const recBid = kw.recommended_bid
                  const diff = recBid && kw.current_bid ? recBid - kw.current_bid : null
                  const diffPct = diff && kw.current_bid ? Math.round(diff / kw.current_bid * 100) : null
                  return (
                    <tr key={kw.id} style={hasProblem ? {background:'rgba(255,79,79,0.04)'} : {}}>
                      <td style={{fontFamily:'monospace',fontSize:11,maxWidth:240}}>
                        <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}
                          title={kw.phrase}>{kw.phrase}</div>
                      </td>
                      <td style={{whiteSpace:'nowrap'}}>{kw.current_bid ? fR(kw.current_bid) : '—'}</td>
                      <td style={{whiteSpace:'nowrap'}}>
                        {recBid ? (
                          <span style={{fontWeight:600,color:diff>0?'var(--green)':'var(--red)'}}>
                            {fR(recBid)}
                            <span style={{fontSize:10,marginLeft:3}}>
                              {diff>0?'▲':'▼'}{Math.abs(diffPct)}%
                            </span>
                          </span>
                        ) : '—'}
                      </td>
                      <td>
                        <span style={{
                          color: kw.avg_position>3?'var(--red)':kw.avg_position<2?'var(--green)':'inherit',
                          fontWeight: kw.avg_position>3?600:400,
                        }}>
                          {fP(kw.avg_position)}
                        </span>
                      </td>
                      <td>{fP(kw.avg_click_position)}</td>
                      <td>{fN(kw.traffic_volume)}</td>
                      <td>{fN(kw.clicks)}</td>
                      <td>
                        {kw.click_delta != null && (
                          <span style={{fontSize:11,color:kw.click_delta>0?'var(--green)':'var(--red)'}}>
                            {kw.click_delta>0?'▲':'▼'}{Math.abs(kw.click_delta)}%
                          </span>
                        )}
                      </td>
                      <td>{kw.ctr != null ? (Math.round(kw.ctr*10)/10)+'%' : '—'}</td>
                      <td>{kw.avg_cpc != null ? fR(kw.avg_cpc) : '—'}</td>
                      <td>{fR(kw.spend)}</td>
                      <td>
                        {kw.problem && (
                          <div style={{fontSize:10}}>
                            <span style={{color:SIGNAL_RULES.find(r=>r.key===kw.problem.type)?.color||'var(--yellow)'}}>●</span>
                            {' '}
                            <span style={{color:'var(--text2)'}}>{kw.problem.description?.slice(0,40)}</span>
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
                {filtered.length > 300 && (
                  <tr>
                    <td colSpan={12} style={{textAlign:'center',color:'var(--text3)',padding:'1rem',fontSize:12}}>
                      Показано 300 из {filtered.length}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        {/* Правая панель */}
        <div style={{display:'flex',flexDirection:'column',gap:12}}>
          <div className="card">
            <div className="card-title">Калькулятор ставки</div>
            <div style={{display:'flex',flexDirection:'column',gap:10}}>
              <label style={{display:'flex',flexDirection:'column',gap:4}}>
                <span style={{fontSize:11,color:'var(--text3)'}}>Целевой CPL, ₽</span>
                <input type="number" value={cpl} onChange={e=>setCpl(Number(e.target.value))} />
              </label>
              <label style={{display:'flex',flexDirection:'column',gap:4}}>
                <span style={{fontSize:11,color:'var(--text3)'}}>Целевой CR, %</span>
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
            <div className="card-title">Сигналы</div>
            {problemCounts.length === 0 ? (
              <div style={{fontSize:12,color:'var(--text3)'}}>Проблем не найдено ✓</div>
            ) : problemCounts.map(r => (
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
            <div style={{fontWeight:500,marginBottom:6}}>ℹ Логика рекомендаций</div>
            <div style={{lineHeight:1.8,color:'var(--text3)'}}>
              <div>↑ Поз. показа &gt;3 → ставка ×1.3</div>
              <div>↓ Поз. показа &lt;1.5 → ставка ×0.9</div>
              <div>Все активные ключи</div>
              <div>✎ ручные &nbsp;⚙ автоматические РК</div>
            </div>
          </div>
        </div>
      </div>
    </Layout>
  )
}
