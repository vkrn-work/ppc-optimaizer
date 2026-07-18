import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

export default function LlmDebug() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [analyses, setAnalyses] = useState([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState(null)

  function load() {
    if (!accountId) return
    setLoading(true)
    api.getAnalyses(accountId, 10)
      .then(data => {
        const llmOnes = (Array.isArray(data) ? data : []).filter(a => a.summary?.source === 'llm')
        setAnalyses(llmOnes)
        setSelected(llmOnes[0] || null)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [accountId])

  const s = selected?.summary || {}

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">ИИ-анализ: вход/выход</div>
        <button className="btn" onClick={load}>↻ Обновить</button>
      </div>

      <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 16 }}>
        Здесь видно, какие данные реально ушли в Claude API и что модель вернула
        ДО применения safety-лимитов (лимиты фильтруют часть предложений — см. поле rejected_by_safety_limits).
      </div>

      {loading ? (
        <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text3)' }}>Загрузка...</div>
      ) : analyses.length === 0 ? (
        <div className="card" style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>
          Ещё не было ни одного ИИ-анализа. Запустите его на странице «Загрузка CRM».
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
            {analyses.map(a => (
              <div
                key={a.id}
                className={`period-tab${selected?.id === a.id ? ' active' : ''}`}
                style={{ cursor: 'pointer' }}
                onClick={() => setSelected(a)}
              >
                {new Date(a.created_at).toLocaleString('ru-RU')} · {a.summary?.provider || '?'}
              </div>
            ))}
          </div>

          <div className="card" style={{ padding: 16, marginBottom: 14 }}>
            <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', fontSize: 13 }}>
              <div><b>Провайдер:</b> {s.provider || '—'}</div>
              <div><b>Модель:</b> {s.model || '—'}</div>
              <div><b>Ключей отправлено:</b> {s.llm_input_full_count ?? '—'}</div>
              <div><b>Изменений от ИИ:</b> {s.llm_raw_output?.length ?? 0}</div>
              <div><b>Прошло в suggestions:</b> {s.suggestions_created ?? '—'}</div>
              <div><b>Отклонено safety-лимитами:</b> {s.rejected_by_safety_limits ?? '—'}</div>
            </div>
            {s.error && (
              <div style={{ marginTop: 10, color: 'var(--red)', fontSize: 13 }}>
                Ошибка вызова LLM: {s.error}
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <div className="card" style={{ padding: 16 }}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>
                📥 Что отправили в модель (первые 15 из {s.llm_input_full_count ?? 0})
              </div>
              <pre style={{
                fontSize: 11, background: 'var(--bg4)', padding: 10, borderRadius: 6,
                overflow: 'auto', maxHeight: 500, whiteSpace: 'pre-wrap',
              }}>
                {JSON.stringify(s.llm_input_sample || [], null, 2)}
              </pre>
            </div>

            <div className="card" style={{ padding: 16 }}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>
                📤 Что вернула модель (сырой ответ, до фильтров)
              </div>
              <pre style={{
                fontSize: 11, background: 'var(--bg4)', padding: 10, borderRadius: 6,
                overflow: 'auto', maxHeight: 500, whiteSpace: 'pre-wrap',
              }}>
                {JSON.stringify(s.llm_raw_output || [], null, 2)}
              </pre>
            </div>
          </div>
        </>
      )}
    </Layout>
  )
}
