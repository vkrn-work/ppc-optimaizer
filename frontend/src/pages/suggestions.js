import { useState, useEffect } from 'react'
import { useRouter } from 'next/router'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

const PRIORITY_META = {
  today:     { label: 'Сегодня',         cls: 'badge-today', order: 0 },
  this_week: { label: 'Эта неделя',      cls: 'badge-week',  order: 1 },
  month:     { label: 'До конца месяца', cls: 'badge-month', order: 2 },
  scale:     { label: 'Масштабирование', cls: 'badge-scale', order: 3 },
}

const CHANGE_LABELS = {
  bid_raise:       { label: 'Поднять ставку',     color: 'var(--green)' },
  bid_lower:       { label: 'Снизить ставку',     color: 'var(--amber)' },
  bid_hold:        { label: 'Держать ставку',     color: 'var(--text-2)' },
  strategy_cpa:    { label: 'Перевести на CPA',   color: 'var(--blue)' },
  add_negatives:   { label: 'Добавить минус-слова', color: 'var(--red)' },
  disable_keyword: { label: 'Отключить ключ',     color: 'var(--red)' },
  expand_semantics:{ label: 'Расширить семантику', color: 'var(--teal)' },
}

export default function Suggestions() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const router = useRouter()
  const filterPriority = router.query.priority || null

  const [suggestions, setSuggestions] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeFilter, setActiveFilter] = useState(filterPriority || 'all')
  const [modal, setModal] = useState(null)
  const [instruction, setInstruction] = useState(null)
  const [rejectReason, setRejectReason] = useState('')
  const [modifyValue, setModifyValue] = useState('')
  const [processing, setProcessing] = useState(false)

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    api.getSuggestions(accountId)
      .then(data => { setSuggestions(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [accountId])

  const filtered = activeFilter === 'all'
    ? suggestions
    : suggestions.filter(s => s.priority === activeFilter)

  const counts = suggestions.reduce((acc, s) => {
    acc[s.priority] = (acc[s.priority] || 0) + 1
    acc.all = (acc.all || 0) + 1
    return acc
  }, {})

  async function doAction(suggestionId, action, extra = {}) {
    setProcessing(true)
    try {
      const result = await api.actionSuggestion(suggestionId, {
        action,
        reject_reason: extra.reason,
        new_value: extra.newValue,
      })
      // Remove from list
      setSuggestions(prev => prev.filter(s => s.id !== suggestionId))
      if (result.instruction) setInstruction(result.instruction)
      setModal(null)
    } catch (e) {
      alert('Ошибка: ' + e.message)
    } finally {
      setProcessing(false)
    }
  }

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.25rem' }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 500 }}>Предложения по изменениям</h1>
          <p style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 2 }}>
            Проверьте каждое предложение и одобрите или отклоните. Изменения применяются вручную в Директе.
          </p>
        </div>
      </div>

      {/* Filter tabs */}
      <div style={{ display: 'flex', gap: 6, marginBottom: '1rem', flexWrap: 'wrap' }}>
        {[
          { key: 'all', label: 'Все' },
          { key: 'today', label: 'Сегодня' },
          { key: 'this_week', label: 'Эта неделя' },
          { key: 'month', label: 'До конца месяца' },
          { key: 'scale', label: 'Масштабирование' },
        ].map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveFilter(tab.key)}
            style={{
              padding: '5px 12px',
              borderRadius: 20,
              fontSize: 12,
              fontWeight: 500,
              border: activeFilter === tab.key ? '1.5px solid var(--text)' : '0.5px solid var(--border-md)',
              background: activeFilter === tab.key ? 'var(--text)' : 'transparent',
              color: activeFilter === tab.key ? '#fff' : 'var(--text-2)',
              cursor: 'pointer',
            }}
          >
            {tab.label}
            {counts[tab.key] ? (
              <span style={{ marginLeft: 6, opacity: 0.7 }}>{counts[tab.key]}</span>
            ) : null}
          </button>
        ))}
      </div>

      {/* Instruction banner */}
      {instruction && (
        <div style={{
          background: 'var(--green-bg)',
          border: '0.5px solid var(--green)',
          borderRadius: 'var(--radius)',
          padding: '1rem',
          marginBottom: '1rem',
        }}>
          <div style={{ fontWeight: 500, color: 'var(--green)', marginBottom: 8 }}>
            Изменение одобрено — инструкция для применения в Директе
          </div>
          <div style={{ fontSize: 13, marginBottom: 8 }}>
            <strong>{instruction.action}</strong>: «{instruction.object}» · {instruction.from} → {instruction.to}
          </div>
          <ol style={{ paddingLeft: 20, fontSize: 12, color: 'var(--text-2)' }}>
            {instruction.steps.map((step, i) => <li key={i} style={{ marginBottom: 3 }}>{step}</li>)}
          </ol>
          <button className="btn" style={{ marginTop: 10, fontSize: 12 }} onClick={() => setInstruction(null)}>
            Закрыть
          </button>
        </div>
      )}

      {/* List */}
      {loading ? (
        <div style={{ color: 'var(--text-3)', padding: '2rem' }}>Загрузка...</div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-3)', padding: '3rem' }}>
          Нет предложений в этой категории
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {filtered.map(s => (
            <SuggestionCard
              key={s.id}
              suggestion={s}
              onApprove={() => doAction(s.id, 'approve')}
              onReject={() => { setModal({ type: 'reject', suggestion: s }); setRejectReason('') }}
              onModify={() => { setModal({ type: 'modify', suggestion: s }); setModifyValue(s.value_after || '') }}
            />
          ))}
        </div>
      )}

      {/* Reject modal */}
      {modal?.type === 'reject' && (
        <ModalOverlay onClose={() => setModal(null)}>
          <div style={{ fontWeight: 500, marginBottom: 12 }}>Отклонить предложение</div>
          <p style={{ fontSize: 13, color: 'var(--text-2)', marginBottom: 12 }}>
            «{modal.suggestion.object_name}» — {modal.suggestion.change_type}
          </p>
          <label style={{ display: 'block', fontSize: 12, color: 'var(--text-2)', marginBottom: 6 }}>
            Причина отклонения
          </label>
          <textarea
            value={rejectReason}
            onChange={e => setRejectReason(e.target.value)}
            rows={3}
            style={{ width: '100%', marginBottom: 12 }}
            placeholder="Например: ключ сезонный, оставить как есть до апреля"
          />
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn" onClick={() => setModal(null)}>Отмена</button>
            <button className="btn btn-danger" disabled={processing}
              onClick={() => doAction(modal.suggestion.id, 'reject', { reason: rejectReason })}>
              Отклонить
            </button>
          </div>
        </ModalOverlay>
      )}

      {/* Modify modal */}
      {modal?.type === 'modify' && (
        <ModalOverlay onClose={() => setModal(null)}>
          <div style={{ fontWeight: 500, marginBottom: 12 }}>Изменить значение</div>
          <p style={{ fontSize: 13, color: 'var(--text-2)', marginBottom: 12 }}>
            «{modal.suggestion.object_name}»
          </p>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16 }}>
            <div>
              <div style={{ fontSize: 11, color: 'var(--text-3)' }}>Было</div>
              <div style={{ fontWeight: 500 }}>{modal.suggestion.value_before}</div>
            </div>
            <div style={{ color: 'var(--text-3)' }}>→</div>
            <div>
              <div style={{ fontSize: 11, color: 'var(--text-3)' }}>Станет</div>
              <input value={modifyValue} onChange={e => setModifyValue(e.target.value)}
                style={{ width: 120 }} />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn" onClick={() => setModal(null)}>Отмена</button>
            <button className="btn btn-success" disabled={processing}
              onClick={() => doAction(modal.suggestion.id, 'modify', { newValue: modifyValue })}>
              Одобрить с изменением
            </button>
          </div>
        </ModalOverlay>
      )}
    </Layout>
  )
}

function SuggestionCard({ suggestion: s, onApprove, onReject, onModify }) {
  const [expanded, setExpanded] = useState(false)
  const pMeta = PRIORITY_META[s.priority] || PRIORITY_META.this_week
  const cMeta = CHANGE_LABELS[s.change_type] || { label: s.change_type, color: 'var(--text)' }

  return (
    <div className="card" style={{ padding: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap' }}>
            <span className={`badge ${pMeta.cls}`}>{pMeta.label}</span>
            <span style={{ fontSize: 13, fontWeight: 500, color: cMeta.color }}>{cMeta.label}</span>
            <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{s.object_type}</span>
          </div>

          <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 4 }}>{s.object_name}</div>

          <div style={{ display: 'flex', gap: 16, fontSize: 13, marginBottom: 6 }}>
            <span>
              <span style={{ color: 'var(--text-3)' }}>Было: </span>
              <span style={{ textDecoration: 'line-through', color: 'var(--text-2)' }}>{s.value_before}</span>
            </span>
            <span>→</span>
            <span>
              <span style={{ color: 'var(--text-3)' }}>Станет: </span>
              <strong style={{ color: cMeta.color }}>{s.value_after}</strong>
            </span>
          </div>

          <p style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 4 }}>{s.rationale}</p>

          {expanded && (
            <div style={{
              background: 'var(--bg)',
              borderRadius: 6,
              padding: '8px 10px',
              fontSize: 12,
              color: 'var(--teal)',
              marginTop: 6,
            }}>
              <strong>Ожидаемый эффект:</strong> {s.expected_effect}
            </div>
          )}

          <button
            onClick={() => setExpanded(v => !v)}
            style={{ fontSize: 11, color: 'var(--text-3)', background: 'none', border: 'none',
              cursor: 'pointer', marginTop: 4, padding: 0 }}>
            {expanded ? '↑ Свернуть' : '↓ Подробнее'}
          </button>
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <button className="btn" onClick={onModify} title="Изменить значение и одобрить"
            style={{ padding: '6px 10px', fontSize: 12 }}>
            Изменить
          </button>
          <button className="btn btn-danger" onClick={onReject}
            style={{ padding: '6px 10px', fontSize: 12 }}>
            ✕
          </button>
          <button className="btn btn-success" onClick={onApprove}
            style={{ padding: '6px 10px', fontSize: 12 }}>
            ✓ Одобрить
          </button>
        </div>
      </div>
    </div>
  )
}

function ModalOverlay({ children, onClose }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.3)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
    }} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={{
        background: 'var(--surface)', borderRadius: 'var(--radius-lg)',
        padding: '1.5rem', width: 440, maxWidth: '95vw',
        boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
      }}>
        {children}
      </div>
    </div>
  )
}
