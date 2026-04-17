import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const PERIODS = [{key:'yesterday',label:'Вчера'},{key:'3d',label:'3 дня'},{key:'week',label:'Неделя'},{key:'month',label:'Месяц'}]

function fR(n) { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }
function fP(n) { return (!n||n===0)?'—':(Math.round(n*10)/10) }

const REASON = {
  low_position:'Позиция показа > 3',
  traffic_drop:'Клики упали > 30%',
  click_position_gap:'Позиция клика хуже показа',
  zero_ctr:'Нет кликов при показах',
  low_ctr:'CTR ниже нормы',
}

export default function Bids() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [period, setPeriod] = useState('week')
  const [keywords, setKeywords] = useState([])
  const [campaigns, setCampaigns] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [selCampaign, setSelCampaign] = useState('')
  const [hideAuto, setHideAuto] = useState(true)
  const [cpl, setCpl] = useState(account?.target_cpl || 2000)
  const [cr, setCr] = useState(5)

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    Promise.all([
      api.getCampaigns(accountId, period),
      api.getKeywords(accountId, `?period=${period}&limit=500${selCampaign?'&campaign_id='+selCampaign:''}${search?'&search='+encodeURIComponent(search):''}`),
    ]).then(([c,k]) => {
      setCampaigns(Array.isArray(c)?c:[])
      setKeywords(Array.isArray(k)?k:[])
    }).catch(console.error).finally(()=>setLoading(false))
  }, [accountId, period, selCampaign, search])

  const filtered = keywords.filter(kw => {
    if (hideAuto && kw.phrase?.includes('---autotargeting')) return false
    return true
  })

  // Только ключи из ручных кампаний имеют смысл для ставок
  const manualCampaignIds = new Set(campaigns.filter(c=>c.strategy_type==='MANUAL_CPC').map(c=>c.id))

  const calcBid = () => Math.round(cpl * (cr / 100))

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Ставки</div>
        <div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
          <input placeholder="Поиск по фразе..." value={search} onChange={e=>setSearch(e.target.value)} style={{width:180}} />
          <select value={selCampaign} onChange={e=>setSelCampaign(e.target.value)} className="btn" style={{padding:'5px 10px'}}>
            <option value="">Все кампании</option>
            {campaigns.filter(c=>c.strategy_type==='MANUAL_CPC').map(c=>(
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <div className="period-tabs">
            {PERIODS.map(p=>(
              <div key={p.key} className={`period-tab${period===p.key?' active':''}`} onClick={()=>setPeriod(p.key)}>{p.label}</div>
            ))}
          </div>
          <button className={`btn${hideAuto?' btn-primary':''}`} onClick={()=>setHideAuto(h=>!h)}>
            {hideAuto?'✓ ':''} Скрыть автотаргетинг
          </button>
        </div>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'1fr 280px',gap:14}}>
        {/* Таблица ключей */}
        <div className="card" style={{padding:0,overflow:'auto'}}>
          {loading ? (
            <div style={{padding:'2rem',textAlign:'center',color:'var(--text3)'}}>Загрузка...</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{minWidth:220}}>Фраза</th>
                  <th>Тек. ставка</th>
                  <th>Рек. ставка</th>
                  <th>Позиция</th>
                  <th>Клики</th>
                  <th>Расход</th>
                  <th>Причина</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length===0 ? (
                  <tr><td colSpan={7} style={{textAlign:'center',color:'var(--text3)',padding:'2rem'}}>
                    Нет данных. Только ручные кампании анализируются.
                  </td></tr>
                ) : filtered.slice(0,300).map(kw=>{
                  const hasProblem = !!kw.problem
                  const recBid = kw.recommended_bid
                  const diff = recBid && kw.current_bid ? recBid - kw.current_bid : null
                  return (
                    <tr key={kw.id} style={hasProblem?{background:'rgba(255,79,79,0.04)'}:{}}>
                      <td style={{fontFamily:'monospace',fontSize:11,maxWidth:260}}>
                        <div style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{kw.phrase}</div>
                      </td>
                      <td>{kw.current_bid ? fR(kw.current_bid) : '—'}</td>
                      <td>
                        {recBid ? (
                          <span style={{fontWeight:600,color:diff>0?'var(--green)':'var(--red)'}}>
                            {fR(recBid)}
                            <span style={{fontSize:10,marginLeft:4}}>
                              {diff>0?'▲':'▼'}{Math.abs(Math.round(diff/kw.current_bid*100))}%
                            </span>
                          </span>
                        ) : '—'}
                      </td>
                      <td>
                        <span style={{color:kw.avg_position>3?'var(--red)':kw.avg_position<2?'var(--green)':'var(--text1)'}}>
                          {fP(kw.avg_position)}
                        </span>
                      </td>
                      <td>
                        <span>{kw.clicks||0}</span>
                        {kw.click_delta!=null && (
                          <span style={{fontSize:10,color:kw.click_delta>0?'var(--green)':'var(--red)',marginLeft:4}}>
                            {kw.click_delta>0?'▲':'▼'}{Math.abs(kw.click_delta)}%
                          </span>
                        )}
                      </td>
                      <td>{fR(kw.spend)}</td>
                      <td style={{fontSize:11,color:'var(--text2)',maxWidth:160}}>
                        {kw.problem ? (
                          <span className="badge badge-warn" style={{fontSize:10}}>
                            {REASON[kw.problem.type] || kw.problem.type}
                          </span>
                        ) : '—'}
                      </td>
                    </tr>
                  )
                })}
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
                <span style={{fontSize:11,color:'var(--text3)'}}>Целевой CR, %</span>
                <input type="number" value={cr} onChange={e=>setCr(Number(e.target.value))} />
              </label>
              <div style={{background:'var(--bg4)',borderRadius:8,padding:'12px 14px'}}>
                <div style={{fontSize:11,color:'var(--text3)',marginBottom:4}}>Рекомендуемая ставка</div>
                <div style={{fontSize:24,fontWeight:700,color:'var(--accent)'}}>{fR(calcBid())}</div>
                <div style={{fontSize:10,color:'var(--text3)',marginTop:4}}>
                  {cpl}₽ × {cr}% = {calcBid()}₽
                </div>
              </div>
            </div>
          </div>

          {/* Статистика проблем */}
          <div className="card">
            <div className="card-title">Требуют внимания</div>
            {(() => {
              const withProblems = filtered.filter(kw=>kw.problem)
              const critical = withProblems.filter(kw=>kw.problem?.severity==='critical').length
              const warning = withProblems.filter(kw=>kw.problem?.severity==='warning').length
              return withProblems.length===0 ? (
                <div style={{fontSize:12,color:'var(--text3)'}}>Проблем не найдено ✓</div>
              ) : (
                <div style={{display:'flex',flexDirection:'column',gap:6}}>
                  {critical>0 && <div style={{display:'flex',justifyContent:'space-between'}}>
                    <span className="badge badge-bad">🔴 Критично</span>
                    <span style={{fontWeight:600}}>{critical}</span>
                  </div>}
                  {warning>0 && <div style={{display:'flex',justifyContent:'space-between'}}>
                    <span className="badge badge-warn">🟡 Важно</span>
                    <span style={{fontWeight:600}}>{warning}</span>
                  </div>}
                  <div style={{fontSize:11,color:'var(--text3)',marginTop:4}}>
                    Всего: {withProblems.length} ключей
                  </div>
                </div>
              )
            })()}
          </div>

          {/* Подсказка */}
          <div className="card" style={{fontSize:11,color:'var(--text2)'}}>
            <div style={{fontWeight:500,marginBottom:6}}>ℹ Логика рекомендаций</div>
            <div style={{lineHeight:1.6}}>
              <div>• Поз. показа &gt;3 → ставка +30%</div>
              <div>• Поз. показа &lt;1.5 → ставка -10%</div>
              <div>• Клики упали &gt;30% → ставка +20%</div>
              <div>• Только ручные кампании</div>
            </div>
          </div>
        </div>
      </div>
    </Layout>
  )
}
