import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const PRIORITY_META = {
  today:     { label: 'Сегодня',         cls: 'badge-today' },
  this_week: { label: 'Эта неделя',      cls: 'badge-week' },
  month:     { label: 'До конца месяца', cls: 'badge-month' },
  scale:     { label: 'Масштабирование', cls: 'badge-scale' },
}

const ACTION_LABELS = {
  bid_raise:        'Поднять ставку',
  bid_lower:        'Снизить ставку',
  bid_hold:         'Держать ставку',
  strategy_cpa:     'Перевести на CPA',
  add_negatives:    'Добавить минус-слова',
  disable_keyword:  'Отключить ключ',
  expand_semantics: 'Расширить семантику',
}

export default function Rules() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [rules, setRules] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!accountId) return
    api.getRules(accountId)
      .then(data => { setRules(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [accountId])

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div style={{ marginBottom: '1.25rem' }}>
        <h1 style={{ fontSize: 20, fontWeight: 500 }}>База правил</h1>
        <p style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 4 }}>
          Правила хранятся в БД и применяются при каждом анализе.
          Глобальные правила работают для всех кабинетов.
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {loading ? (
          <div style={{ color: 'var(--text-3)', padding: '2rem' }}>Загрузка...</div>
        ) : rules.map(r => {
          const pMeta = PRIORITY_META[r.priority] || PRIORITY_META.this_week
          return (
            <div key={r.id} className="card" style={{ padding: '1rem' }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'center' }}>
                    <span className={`badge ${pMeta.cls}`}>{pMeta.label}</span>
                    {r.is_global && (
                      <span className="badge badge-info">Глобальное</span>
                    )}
                    <span style={{ fontWeight: 500, fontSize: 13 }}>{r.name}</span>
                  </div>
                  <p style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 4 }}>{r.description}</p>
                  <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
                    Условие: <code style={{ fontSize: 11 }}>{r.condition_type}</code>
                    {' → '}
                    Действие: <strong style={{ color: 'var(--text)' }}>
                      {ACTION_LABELS[r.action_type] || r.action_type}
                    </strong>
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </Layout>
  )
}
