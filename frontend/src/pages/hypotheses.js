import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const STATUS_CFG = {
  planned:  {label:'Запланирована',cls:'badge-info'},
  active:   {label:'В работе',     cls:'badge-warn'},
  checking: {label:'Проверяется',  cls:'badge-info'},
  success:  {label:'✅ Успешная',  cls:'badge-ok'},
  failed:   {label:'❌ Неуспешная',cls:'badge-bad'},
  neutral:  {label:'⚠️ Нейтральная',cls:'badge-warn'},
}
const SOURCE_CFG = {
  algorithm:{label:'🤖 Алгоритм',cls:'badge-info'},
  manual:   {label:'✍️ Ручная',   cls:'badge-warn'},
  suggestion:{label:'📋 Предложение',cls:'badge-info'},
}

const TYPE_LABELS = {
  low_position:'Позиция',traffic_drop:'Трафик',zero_ctr:'CTR=0',
  low_ctr:'CTR',click_position_gap:'Поз.клика',
}

export default function Hypotheses() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [hyps, setHyps] = useState([])
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState('active')
  const [showNew, setShowNew] = useState(false)
  const [newH, setNewH] = useState({phrase:'',change_description:'',forecast:''})
  const [saving, setSaving] = useState(false)

  const load = () => {
    if (!accountId) return
    setLoading(true)
    api.getHypotheses(accountId)
      .then(d => setHyps(Array.isArray(d)?d:[]))
      .catch(console.error)
      .finally(()=>setLoading(false))
  }

  useEffect(()=>{ load() }, [accountId])

  const filtered = hyps.filter(h => {
    if (tab==='active') return ['planned','active','checking'].includes(h.status)
    return ['success','failed','neutral'].includes(h.status)
  })

  async function saveNew() {
    if (!newH.phrase||!newH.change_description) return
    setSaving(true)
    try {
      await api.createHypothesis(accountId, {...newH, source:'manual'})
      setShowNew(false)
      setNewH({phrase:'',change_description:'',forecast:''})
      load()
    } catch(e) { alert('Ошибка: '+e.message) }
    finally { setSaving(false) }
  }

  const activeCnt = hyps.filter(h=>['planned','active','checking'].includes(h.status)).length
  const doneCnt   = hyps.filter(h=>['success','failed','neutral'].includes(h.status)).length

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Гипотезы</div>
        <button className="btn btn-primary" onClick={()=>setShowNew(s=>!s)}>
          + Новая гипотеза
        </button>
      </div>

      {showNew && (
        <div className="card" style={{marginBottom:14}}>
          <div style={{fontWeight:500,marginBottom:12,fontSize:14}}>Создать гипотезу</div>
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
            <label style={{display:'flex',flexDirection:'column',gap:4,gridColumn:'1/-1'}}>
              <span style={{fontSize:11,color:'var(--text3)'}}>Ключ / объект *</span>
              <input value={newH.phrase} onChange={e=>setNewH(n=>({...n,phrase:e.target.value}))} placeholder="+фраза или кампания" />
            </label>
            <label style={{display:'flex',flexDirection:'column',gap:4}}>
              <span style={{fontSize:11,color:'var(--text3)'}}>Планируемое изменение *</span>
              <input value={newH.change_description} onChange={e=>setNewH(n=>({...n,change_description:e.target.value}))} placeholder="Что изменить" />
            </label>
            <label style={{display:'flex',flexDirection:'column',gap:4}}>
              <span style={{fontSize:11,color:'var(--text3)'}}>Ожидаемый эффект</span>
              <input value={newH.forecast} onChange={e=>setNewH(n=>({...n,forecast:e.target.value}))} placeholder="Прогноз показателей" />
            </label>
          </div>
          <div style={{display:'flex',gap:8,marginTop:10,justifyContent:'flex-end'}}>
            <button className="btn" onClick={()=>setShowNew(false)}>Отмена</button>
            <button className="btn btn-primary" onClick={saveNew} disabled={saving||!newH.phrase||!newH.change_description}>
              {saving?'Сохранение...':'Сохранить'}
            </button>
          </div>
        </div>
      )}

      <div className="period-tabs" style={{marginBottom:14,display:'inline-flex'}}>
        <div className={`period-tab${tab==='active'?' active':''}`} onClick={()=>setTab('active')}>
          Активные ({activeCnt})
        </div>
        <div className={`period-tab${tab==='done'?' active':''}`} onClick={()=>setTab('done')}>
          Завершённые ({doneCnt})
        </div>
      </div>

      {loading ? (
        <div style={{color:'var(--text3)',fontSize:13}}>Загрузка...</div>
      ) : filtered.length===0 ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-icon">◇</div>
            <div className="empty-title">Нет гипотез</div>
            <div className="empty-desc">
              {tab==='active'
                ? 'Возьмите предложение в работу или создайте свою гипотезу'
                : 'Завершённые гипотезы появятся здесь'}
            </div>
          </div>
        </div>
      ) : (
        <div style={{display:'flex',flexDirection:'column',gap:8}}>
          {filtered.map(h => {
            const statusCfg = STATUS_CFG[h.status] || {label:h.status,cls:'badge-info'}
            const sourceCfg = SOURCE_CFG[h.source] || SOURCE_CFG.manual
            return (
              <div key={h.id} className="card">
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start'}}>
                  <div style={{flex:1}}>
                    <div style={{display:'flex',gap:6,alignItems:'center',marginBottom:6,flexWrap:'wrap'}}>
                      <span className={`badge ${statusCfg.cls}`}>{statusCfg.label}</span>
                      <span className={`badge ${sourceCfg.cls}`}>{sourceCfg.label}</span>
                      {h.problem_type && (
                        <span style={{fontSize:10,background:'var(--bg4)',color:'var(--text3)',padding:'1px 5px',borderRadius:3}}>
                          {TYPE_LABELS[h.problem_type]||h.problem_type}
                        </span>
                      )}
                    </div>
                    <div style={{fontWeight:500,marginBottom:4,fontSize:14}}>{h.phrase}</div>
                    <div style={{fontSize:13,color:'var(--text2)',marginBottom:4}}>
                      <b>Изменение:</b> {h.change_description}
                    </div>
                    {h.forecast && (
                      <div style={{fontSize:13,color:'var(--text2)'}}>
                        <b>Прогноз:</b> {h.forecast}
                      </div>
                    )}
                    {/* Результаты если есть */}
                    {h.metrics_before && h.metrics_after && (
                      <div style={{display:'flex',gap:20,marginTop:10,fontSize:12,padding:'8px 0',borderTop:'1px solid var(--border)'}}>
                        <div style={{color:'var(--text3)'}}>
                          До: {h.metrics_before.clicks} кликов
                          {h.metrics_before.spend ? `, ${Math.round(h.metrics_before.spend)}₽` : ''}
                        </div>
                        <div style={{color:'var(--green)'}}>
                          После: {h.metrics_after.clicks} кликов
                          {h.metrics_after.spend ? `, ${Math.round(h.metrics_after.spend)}₽` : ''}
                          {h.metrics_before.clicks > 0 && (
                            <span style={{marginLeft:6}}>
                              {h.metrics_after.clicks > h.metrics_before.clicks ? '▲' : '▼'}
                              {Math.abs(Math.round((h.metrics_after.clicks-h.metrics_before.clicks)/h.metrics_before.clicks*100))}%
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                  <div style={{fontSize:11,color:'var(--text3)',marginLeft:12,flexShrink:0,textAlign:'right'}}>
                    <div>{h.created_at ? new Date(h.created_at).toLocaleDateString('ru') : ''}</div>
                    {h.check_after && (
                      <div style={{marginTop:4}}>
                        Проверка: {new Date(h.check_after).toLocaleDateString('ru')}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Layout>
  )
}
