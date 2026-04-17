import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const BASE = 'https://ppc-optimaizer-production.up.railway.app'

export default function Diagnostics() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [health, setHealth] = useState(null)
  const [diag, setDiag] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      fetch(`${BASE}/health`).then(r=>r.json()).catch(()=>({status:'error'})),
      accountId ? api.getDiagnostics(accountId).catch(()=>null) : Promise.resolve(null),
    ]).then(([h,d]) => {
      setHealth(h)
      setDiag(d)
    }).finally(()=>setLoading(false))
  }, [accountId])

  const checks = [
    {
      label: 'API бэкенда',
      ok: health?.status==='ok',
      detail: health?.status==='ok' ? `Версия: ${health.version||'?'}, DB: ${health.db||'?'}` : 'Недоступен',
    },
    {
      label: 'База данных',
      ok: health?.db==='ok',
      detail: health?.db==='ok' ? 'Подключена' : health?.db||'Неизвестно',
    },
    {
      label: 'Токен Директа',
      ok: !!account?.oauth_token,
      detail: account?.oauth_token ? 'Настроен ✓' : 'Не настроен — перейдите в Кабинеты',
    },
    {
      label: 'Метрика',
      ok: !!account?.metrika_counter_id,
      detail: account?.metrika_counter_id ? `Счётчик ${account.metrika_counter_id} ✓` : 'Не настроена',
    },
    {
      label: 'Последний сбор',
      ok: !!account?.last_sync_at,
      detail: account?.last_sync_at
        ? new Date(account.last_sync_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}) + ' МСК'
        : 'Ещё не запускался',
    },
    {
      label: 'Воркер (Celery)',
      ok: health?.worker_ok !== false,
      detail: health?.worker_ok===true ? 'Работает ✓' : 'Статус неизвестен',
    },
  ]

  const errorCount = checks.filter(c=>!c.ok).length

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div>
          <div className="page-title">Диагностика</div>
          <div style={{fontSize:12,color:'var(--text3)',marginTop:2}}>Состояние системы</div>
        </div>
        {errorCount > 0 && (
          <span className="badge badge-bad">⚠ {errorCount} проблем{errorCount>1?'ы':''}</span>
        )}
      </div>

      {loading ? (
        <div style={{color:'var(--text3)',fontSize:13}}>Проверка...</div>
      ) : (
        <div style={{display:'flex',flexDirection:'column',gap:8,maxWidth:600}}>
          {checks.map((c,i) => (
            <div key={i} className="card" style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'12px 16px'}}>
              <div style={{display:'flex',alignItems:'center',gap:12}}>
                <div style={{
                  width:10,height:10,borderRadius:'50%',flexShrink:0,
                  background:c.ok?'var(--green)':'var(--red)',
                  boxShadow:c.ok?'0 0 0 3px rgba(34,201,138,0.15)':'0 0 0 3px rgba(255,79,79,0.15)',
                }} />
                <div>
                  <div style={{fontWeight:500,fontSize:13}}>{c.label}</div>
                  <div style={{fontSize:12,color:'var(--text3)'}}>{c.detail}</div>
                </div>
              </div>
              <span className={`badge ${c.ok?'badge-ok':'badge-bad'}`}>{c.ok?'OK':'Ошибка'}</span>
            </div>
          ))}

          {/* Лог ошибок */}
          {diag?.errors?.length > 0 && (
            <div className="card" style={{marginTop:8}}>
              <div style={{fontWeight:500,fontSize:13,marginBottom:10}}>Последние ошибки</div>
              {diag.errors.map((e,i) => (
                <div key={i} style={{
                  fontSize:11,padding:'6px 8px',background:'var(--red-bg)',borderRadius:4,
                  marginBottom:4,color:'var(--red)',fontFamily:'monospace',
                }}>{e}</div>
              ))}
            </div>
          )}

          {/* Подсказки */}
          {errorCount > 0 && (
            <div className="card" style={{background:'var(--blue-bg)',border:'1px solid var(--blue-bg)'}}>
              <div style={{fontSize:13,fontWeight:500,marginBottom:6,color:'var(--blue)'}}>💡 Что сделать</div>
              {!account?.oauth_token && (
                <div style={{fontSize:12,marginBottom:4}}>
                  → <a href="/settings" style={{color:'var(--accent)'}}>Настройки</a> — добавьте OAuth-токен Яндекс Директ
                </div>
              )}
              {!account?.metrika_counter_id && (
                <div style={{fontSize:12,marginBottom:4}}>
                  → <a href="/settings" style={{color:'var(--accent)'}}>Настройки</a> — добавьте ID счётчика Метрики
                </div>
              )}
              {!account?.last_sync_at && (
                <div style={{fontSize:12}}>
                  → Нажмите «Обновить данные» в верхней панели для первого сбора
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </Layout>
  )
}
