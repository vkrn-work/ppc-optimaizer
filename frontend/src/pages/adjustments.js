import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

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

export default function Adjustments() {
  const { account, accounts, accountId, switchAccount } = useAccount()
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
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Корректировки</div>
        <div style={{fontSize:12,color:'var(--text3)'}}>Данные из Яндекс Метрики</div>
      </div>
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
        <div className="card" style={{padding: tab==='Время'||tab==='Дни недели'?0:0, overflow:'auto'}}>
          {tab==='Устройства' && renderDevices()}
          {tab==='Регионы' && renderRegions()}
          {tab==='Время' && renderTime()}
          {tab==='Дни недели' && renderWeekdays()}
        </div>
      )}
    </Layout>
  )
}
