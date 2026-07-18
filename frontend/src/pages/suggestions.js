import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

// v2.1: страница переведена на реальную таблицу suggestions (была на
// analysis.problems/opportunities — старый JSON-формат rule-based анализатора,
// который LLM-анализатор не заполняет вообще, отсюда "предложения пустые"
// после LLM-прогона). Действия теперь реальные: accept/reject через
// /suggestions/{id}/action, применение в кабинет через /suggestions/{id}/apply
// (было: createHypothesis — ничего не одобряло и не применяло по-настоящему).

const STATUS_TABS = [
  { key: 'pending',  label: 'Ожидают' },
  { key: 'approved', label: 'Одобрены' },
  { key: 'applied',  label: 'Применены' },
  { key: 'rejected', label: 'Отклонены' },
]

const CHANGE_TYPE_LABELS = {
  bid_raise:      '📈 Поднять ставку',
  bid_lower:      '📉 Снизить ставку',
  add_negatives:  '🚫 Минус-слова',
  pause:          '⏸ Остановить',
  ad_rewrite:     '✏️ Переписать объявление',
  ad_test:        '🧪 A/B тест объявления',
  flag_ad_issue:  '⚠️ Проблема с объявлением',
  flag_ctr_issue: '⚠️ Проблема с CTR',
  suggest_bid_increase: '📈 Поднять ставку',
  check:          '🔍 Проверить',
}

const PRI_LABELS = {
  today:     '🔴 Сегодня',
  this_week: '🟡 Эта неделя',
  month:     '🔵 Месяц',
  scale:     '🟢 Масштабирование',
}

function fR(n) { return n==null?'—':Math.round(n).toLocaleString('ru')+' ₽' }

export default function Suggestions() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [status, setStatus]   = useState('pending')
  const [items, setItems]     = useState([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy]       = useState({})
  const [expanded, setExpanded] = useState(new Set())
  const [filters, setFilters] = useState({ priority:'', changeType:'', search:'' })

  function load() {
    if (!accountId) return
    setLoading(true)
    api.getSuggestions(accountId, `?status=${status}`)
      .then(rows => setItems(Array.isArray(rows) ? rows : []))
      .catch(console.error)
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [accountId, status])

  const setF = (k,v) => setFilters(f=>({...f,[k]:v}))

  const filtered = items.filter(item => {
    if (filters.priority && item.priority !== filters.priority) return false
    if (filters.changeType && item.change_type !== filters.changeType) return false
    if (filters.search) {
      const q = filters.search.toLowerCase()
      const txt = (item.phrase||'')+' '+(item.description||'')+' '+(item.action||'')
      if (!txt.toLowerCase().includes(q)) return false
    }
    return true
  })

  const changeTypesInData = [...new Set(items.map(i=>i.change_type).filter(Boolean))]

  const byPriority = {
    today:     filtered.filter(i=>i.priority==='today'),
    this_week: filtered.filter(i=>i.priority==='this_week'),
    month:     filtered.filter(i=>i.priority==='month'),
    scale:     filtered.filter(i=>i.priority==='scale' || !i.priority),
  }

  async function approve(item) {
    setBusy(b=>({...b,[item.id]:true}))
    try {
      await api.actionSuggestion(item.id, { action: 'accept' })
      setItems(prev => prev.filter(x=>x.id!==item.id))
    } catch(e) {
      alert('Ошибка: ' + e.message)
    } finally {
      setBusy(b=>({...b,[item.id]:false}))
    }
  }

  async function reject(item) {
    const reason = window.prompt('Причина отклонения (необязательно):') || undefined
    setBusy(b=>({...b,[item.id]:true}))
    try {
      await api.actionSuggestion(item.id, { action: 'reject', reason })
      setItems(prev => prev.filter(x=>x.id!==item.id))
    } catch(e) {
      alert('Ошибка: ' + e.message)
    } finally {
      setBusy(b=>({...b,[item.id]:false}))
    }
  }

  async function applyToDirect(item) {
    if (!window.confirm(`Применить изменение в кабинете Директа?\n\n${item.phrase}\n${item.value_before} → ${item.value_after}`)) return
    setBusy(b=>({...b,[item.id]:true}))
    try {
      const res = await api.applySuggestion(item.id)
      if (res.status === 'applied') {
        setItems(prev => prev.map(x=>x.id===item.id ? {...x, status:'applied'} : x))
        alert('Применено в кабинете Директа: ' + (res.detail || 'OK'))
      } else {
        alert('Не применилось: ' + (res.detail || JSON.stringify(res)))
      }
    } catch(e) {
      alert('Ошибка применения: ' + e.message)
    } finally {
      setBusy(b=>({...b,[item.id]:false}))
    }
  }

  function toggleExpanded(id) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function SuggestionCard({ item }) {
    const isOpen = expanded.has(item.id)
    const isBusy = busy[item.id]
    const sev = item.severity || 'warning'
    const borderColor = sev==='critical'?'var(--red)':sev==='warning'?'#e07b00':sev==='info'?'var(--accent)':'var(--green)'

    return (
      <div style={{
        border: `1px solid var(--border)`,
        borderLeft: `3px solid ${borderColor}`,
        borderRadius: 8,
        padding: '12px 14px',
        background: 'var(--bg2)',
        cursor: 'pointer',
      }} onClick={() => toggleExpanded(item.id)}>

        <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',gap:8}}>
          <div style={{flex:1,minWidth:0}}>
            <div style={{display:'flex',gap:6,flexWrap:'wrap',marginBottom:4,alignItems:'center'}}>
              <span style={{fontSize:11,fontWeight:600,color:borderColor}}>
                {CHANGE_TYPE_LABELS[item.change_type] || item.change_type || '—'}
              </span>
              {item.priority && (
                <span style={{fontSize:10,color:'var(--text3)'}}>{PRI_LABELS[item.priority]}</span>
              )}
              {item.status && item.status !== 'pending' && (
                <span style={{fontSize:10,background:'var(--bg4)',color:'var(--text3)',
                  padding:'1px 5px',borderRadius:3}}>
                  {item.status}
                </span>
              )}
            </div>

            <div style={{fontFamily:'monospace',fontSize:11,fontWeight:500,
              overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',
              maxWidth:'100%',color:'var(--text1)',marginBottom:4}}
              title={item.phrase}>
              {item.phrase || '—'}
            </div>

            <div style={{fontSize:12,color:'var(--text2)',marginBottom:2}}>
              {item.description}
            </div>

            {(item.value_before || item.value_after) && (
              <div style={{fontSize:12,color:'var(--accent)',fontWeight:500}}>
                {item.value_before || '—'} → {item.value_after || '—'}
              </div>
            )}
          </div>

          <div style={{display:'flex',flexDirection:'column',gap:4,flexShrink:0,marginTop:2}}
            onClick={e=>e.stopPropagation()}>
            {item.status === 'pending' && (
              <>
                <button className="btn btn-sm btn-primary" onClick={()=>approve(item)} disabled={isBusy}>
                  {isBusy?'⏳':'✓ Одобрить'}
                </button>
                <button className="btn btn-sm" onClick={()=>reject(item)} disabled={isBusy}>
                  ✕ Отклонить
                </button>
              </>
            )}
            {item.status === 'approved' && (
              <button className="btn btn-sm btn-primary" onClick={()=>applyToDirect(item)} disabled={isBusy}>
                {isBusy?'⏳':'🚀 Применить в Директе'}
              </button>
            )}
            {item.status === 'applied' && (
              <span style={{fontSize:11,color:'var(--green)'}}>✓ Применено</span>
            )}
            {item.status === 'rejected' && (
              <span style={{fontSize:11,color:'var(--text3)'}}>✕ Отклонено</span>
            )}
          </div>
        </div>

        {isOpen && (
          <div style={{marginTop:10,paddingTop:10,borderTop:'1px solid var(--border)'}}
            onClick={e=>e.stopPropagation()}>
            {item.rationale && (
              <div style={{fontSize:12,marginBottom:6}}>
                <span style={{color:'var(--text3)',fontWeight:500}}>Обоснование: </span>
                <span style={{color:'var(--text2)'}}>{item.rationale}</span>
              </div>
            )}
            {item.expected_effect && (
              <div style={{fontSize:12,marginBottom:6}}>
                <span style={{color:'var(--text3)',fontWeight:500}}>Ожидаем: </span>
                <span style={{color:'var(--text2)'}}>{item.expected_effect}</span>
              </div>
            )}
            {item.recommended_bid != null && (
              <div style={{fontSize:12,marginBottom:6}}>
                <span style={{color:'var(--text3)',fontWeight:500}}>Рекомендованная ставка: </span>
                <span style={{color:'var(--text1)'}}>{fR(item.recommended_bid)}</span>
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  const anyFilters = filters.priority || filters.changeType || filters.search

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Предложения</div>
        <div className="period-tabs">
          {STATUS_TABS.map(t=>(
            <div key={t.key} className={`period-tab${status===t.key?' active':''}`}
              onClick={()=>setStatus(t.key)}>{t.label}</div>
          ))}
        </div>
      </div>

      <div style={{display:'flex',gap:8,marginBottom:14,flexWrap:'wrap',alignItems:'center'}}>
        <input placeholder="Поиск по ключу или описанию..." value={filters.search}
          onChange={e=>setF('search',e.target.value)} style={{width:220}} />

        <select value={filters.priority} onChange={e=>setF('priority',e.target.value)}
          className="btn" style={{padding:'5px 10px'}}>
          <option value="">Все приоритеты</option>
          <option value="today">🔴 Сегодня</option>
          <option value="this_week">🟡 Эта неделя</option>
          <option value="month">🔵 Месяц</option>
          <option value="scale">🟢 Масштаб</option>
        </select>

        <select value={filters.changeType} onChange={e=>setF('changeType',e.target.value)}
          className="btn" style={{padding:'5px 10px'}}>
          <option value="">Все типы изменений</option>
          {changeTypesInData.map(t=>(
            <option key={t} value={t}>{CHANGE_TYPE_LABELS[t]||t}</option>
          ))}
        </select>

        {anyFilters && (
          <button className="btn"
            onClick={()=>setFilters({priority:'',changeType:'',search:''})}>
            × Сбросить
          </button>
        )}

        <span style={{fontSize:11,color:'var(--text3)',marginLeft:'auto'}}>
          {filtered.length} из {items.length}
        </span>
      </div>

      {loading ? (
        <div style={{color:'var(--text3)',fontSize:13,padding:'2rem',textAlign:'center'}}>
          Загрузка...
        </div>
      ) : items.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-icon">◈</div>
            <div className="empty-title">
              {status==='pending' ? 'Нет предложений, ожидающих решения' : 'Пусто'}
            </div>
            <div className="empty-desc">
              {status==='pending'
                ? 'Запустите ИИ-анализ или обычный анализ на странице «Загрузка CRM»'
                : 'В этом статусе пока ничего нет'}
            </div>
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{padding:'2rem',textAlign:'center',color:'var(--text3)'}}>
          Нет предложений по выбранным фильтрам
        </div>
      ) : (
        <div style={{display:'flex',flexDirection:'column',gap:20}}>
          {[
            {key:'today',     items:byPriority.today},
            {key:'this_week', items:byPriority.this_week},
            {key:'month',     items:byPriority.month},
            {key:'scale',     items:byPriority.scale},
          ].filter(g=>g.items.length>0).map(group => (
            <div key={group.key}>
              <div style={{fontSize:13,fontWeight:600,marginBottom:8,color:'var(--text2)'}}>
                {PRI_LABELS[group.key]} — {group.items.length} {group.items.length===1?'предложение':'предложений'}
              </div>
              <div style={{display:'flex',flexDirection:'column',gap:6}}>
                {group.items.map(item => (
                  <SuggestionCard key={item.id} item={item} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Layout>
  )
}
