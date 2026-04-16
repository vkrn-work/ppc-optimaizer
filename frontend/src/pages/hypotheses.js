import { useState } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'

const MOCK = [
  { id: 1, status: 'planned', phrase: '+листы +S690QL', change: 'Поднять ставку с 362₽ до 430₽', forecast: '+2-3 клика/нед, позиция 1-2', created: '2026-04-17', source: 'algorithm' },
  { id: 2, status: 'done', phrase: '+Сталь +S700MC', change: 'Поднять ставку с 47₽ до 310₽', forecast: '+8-12 кликов/нед', created: '2026-04-10', verdict: 'success', before: { clicks: 2, spend: 890 }, after: { clicks: 11, spend: 3410 }, source: 'algorithm' },
  { id: 3, status: 'done', phrase: '+EN +10088', change: 'Отключить 2 дублирующих ключа', forecast: '-30% CPC группы', created: '2026-04-05', verdict: 'neutral', source: 'manual' },
]

const STATUS = {
  planned: { label: 'Запланирована', cls: 'badge-info' },
  active: { label: 'В работе', cls: 'badge-warn' },
  done: { label: 'Завершена', cls: 'badge-ok' },
}
const VERDICT = {
  success: { label: '✅ Успешная', cls: 'badge-ok' },
  failed: { label: '❌ Неуспешная', cls: 'badge-bad' },
  neutral: { label: '⚠️ Нейтральная', cls: 'badge-warn' },
  insufficient: { label: '⏳ Мало данных', cls: 'badge-info' },
}

export default function Hypotheses() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [tab, setTab] = useState('planned')
  const [showNew, setShowNew] = useState(false)
  const [newH, setNewH] = useState({ phrase: '', change: '', forecast: '' })

  const filtered = MOCK.filter(h => tab === 'planned' ? h.status === 'planned' : h.status === 'done')

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Гипотезы</div>
        <button className="btn btn-primary" onClick={() => setShowNew(s => !s)}>
          + Новая гипотеза
        </button>
      </div>

      {showNew && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div style={{ fontWeight: 500, marginBottom: 12 }}>Создать гипотезу</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, gridColumn: '1/-1' }}>
              <span style={{ fontSize: 11, color: 'var(--text3)' }}>Ключ / объект</span>
              <input value={newH.phrase} onChange={e => setNewH(n => ({...n, phrase: e.target.value}))} placeholder="+фраза или кампания" />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--text3)' }}>Планируемое изменение</span>
              <input value={newH.change} onChange={e => setNewH(n => ({...n, change: e.target.value}))} placeholder="Что изменить" />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--text3)' }}>Ожидаемый эффект</span>
              <input value={newH.forecast} onChange={e => setNewH(n => ({...n, forecast: e.target.value}))} placeholder="Прогноз показателей" />
            </label>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 10, justifyContent: 'flex-end' }}>
            <button className="btn" onClick={() => setShowNew(false)}>Отмена</button>
            <button className="btn btn-primary">Сохранить</button>
          </div>
        </div>
      )}

      <div className="period-tabs" style={{ marginBottom: 14, display: 'inline-flex' }}>
        <div className={`period-tab${tab === 'planned' ? ' active' : ''}`} onClick={() => setTab('planned')}>
          Запланированы ({MOCK.filter(h => h.status === 'planned').length})
        </div>
        <div className={`period-tab${tab === 'done' ? ' active' : ''}`} onClick={() => setTab('done')}>
          Реализованы ({MOCK.filter(h => h.status === 'done').length})
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {filtered.length === 0 ? (
          <div className="card">
            <div className="empty-state">
              <div className="empty-icon">◇</div>
              <div className="empty-title">Нет гипотез</div>
              <div className="empty-desc">Возьмите предложение в работу или создайте свою гипотезу</div>
            </div>
          </div>
        ) : filtered.map(h => (
          <div key={h.id} className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
                  <span className={`badge ${STATUS[h.status]?.cls}`}>{STATUS[h.status]?.label}</span>
                  {h.verdict && <span className={`badge ${VERDICT[h.verdict]?.cls}`}>{VERDICT[h.verdict]?.label}</span>}
                  <span className={`badge ${h.source === 'algorithm' ? 'badge-info' : 'badge-warn'}`}>
                    {h.source === 'algorithm' ? '🤖 Алгоритм' : '✍️ Ручная'}
                  </span>
                </div>
                <div style={{ fontWeight: 500, marginBottom: 4 }}>{h.phrase}</div>
                <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 4 }}>
                  <b>Изменение:</b> {h.change}
                </div>
                <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                  <b>Прогноз:</b> {h.forecast}
                </div>
                {h.before && h.after && (
                  <div style={{ display: 'flex', gap: 20, marginTop: 10, fontSize: 12 }}>
                    <div style={{ color: 'var(--text3)' }}>
                      До: {h.before.clicks} кликов, {h.before.spend}₽
                    </div>
                    <div style={{ color: 'var(--green)' }}>
                      После: {h.after.clicks} кликов, {h.after.spend}₽
                      <span style={{ marginLeft: 6 }}>▲ +{Math.round((h.after.clicks - h.before.clicks) / h.before.clicks * 100)}% кликов</span>
                    </div>
                  </div>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text3)', marginLeft: 12, flexShrink: 0 }}>
                {h.created}
              </div>
            </div>
          </div>
        ))}
      </div>
    </Layout>
  )
}
