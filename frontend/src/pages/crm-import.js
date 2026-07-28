import { useState, useRef, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

export default function CrmImport() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [llmRunning, setLlmRunning] = useState(false)
  const [llmMessage, setLlmMessage] = useState('')
  const [providers, setProviders] = useState([])
  const [provider, setProvider] = useState('claude')
  const fileRef = useRef(null)

  useEffect(() => {
    api.getLLMProviders()
      .then(list => {
        setProviders(list)
        const firstConfigured = list.find(p => p.configured)
        if (firstConfigured) setProvider(firstConfigured.id)
      })
      .catch(console.error)
  }, [])

  async function handleUpload(e) {
    e.preventDefault()
    if (!file || !accountId) return
    setUploading(true); setError(''); setResult(null)
    try {
      const res = await api.importCRM(accountId, file)
      setResult(res)
      setFile(null)
      if (fileRef.current) fileRef.current.value = ''
    } catch (e) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  async function handleRunLLM() {
    if (!accountId) return
    setLlmRunning(true); setLlmMessage('')
    try {
      const res = await api.runLLMAnalysis(accountId, 28, provider)
      setLlmMessage(res.message || 'Анализ запущен. Проверьте раздел «Предложения» через 1-2 минуты.')
    } catch (e) {
      setLlmMessage('Ошибка: ' + e.message)
    } finally {
      setLlmRunning(false)
    }
  }

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Загрузка выгрузки из CRM</div>
      </div>

      <div className="card" style={{ padding: 20, marginBottom: 16 }}>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 14, lineHeight: 1.5 }}>
          Загрузите CSV или XLSX-файл с заявками из вашей CRM. Нужна колонка со статусом
          заявки — любой текст, ваши реальные статусы автоматически разбиваются на воронку
          lead / MQL / SQL. Для сопоставления с ключевыми словами достаточно либо колонки
          с чистым ключевым словом (utm_term), либо колонки «Источник» с полной цепочкой
          (кабинет → площадка → кампания → группа → id объявления → фраза) — она разбирается
          автоматически и даёт более точный матчинг по номеру объявления. Порядок колонок
          не важен, названия могут быть на русском (например, «Статус», «Источник», «Сумма сделки»).
        </div>

        <form onSubmit={handleUpload}>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.xlsx,.xlsm"
            onChange={e => setFile(e.target.files?.[0] || null)}
            style={{ marginBottom: 12, display: 'block' }}
          />
          <button className="btn btn-primary" type="submit" disabled={!file || uploading}>
            {uploading ? 'Загрузка...' : 'Загрузить файл'}
          </button>
        </form>

        {error && (
          <div style={{ marginTop: 12, color: 'var(--red)', fontSize: 13 }}>{error}</div>
        )}

        {result && (
          <div style={{ marginTop: 14, fontSize: 13, color: 'var(--text2)', lineHeight: 1.7 }}>
            {result.message && (
              <div style={{ marginBottom: 8, padding: '8px 10px', borderRadius: 6,
                background: 'var(--bg4)', color: 'var(--text)' }}>{result.message}</div>
            )}
            <div>✓ Импортировано новых заявок: <b>{result.imported}</b> из {result.total_rows} строк в файле</div>
            <div>Воронка: MQL — <b>{result.mql_count}</b>, SQL (КП/БП) — <b>{result.sql_count}</b></div>
            <div style={{ marginTop: 6 }}>Сопоставлено с кампанией: <b>{result.matched_by_campaign_only}</b>,
              с группой: <b>{result.matched_by_ad_group}</b>,
              с ключом (запрос/фраза/объявление): <b>{(result.matched_by_search_query||0)+(result.matched_by_phrase||0)+(result.matched_by_ad_id||0)}</b></div>
            <div>Совсем не сопоставлено: <b>{result.unmatched}</b></div>
            <div style={{ marginTop: 6 }}>Пропущено (нет статуса): <b>{result.skipped_empty_status}</b>,
              дубли: <b>{result.skipped_duplicate}</b>{result.date_parse_failed ? <> , не распознана дата: <b>{result.date_parse_failed}</b></> : null}</div>
            {result.reattribution && result.reattribution.total != null && (
              <div style={{ marginTop: 6, color: 'var(--text3)' }}>
                Пересчёт разноски: всего заявок <b>{result.reattribution.total}</b>,
                получили кампанию <b>{result.reattribution.gained_campaign}</b>,
                группу <b>{result.reattribution.gained_ad_group}</b>,
                ключ <b>{result.reattribution.gained_keyword}</b>
              </div>
            )}
            {result.columns_ignored && result.columns_ignored.length > 0 && (
              <div style={{ marginTop: 6, color: 'var(--text3)', fontSize: 12 }}>
                Колонки файла, которые система не использовала: {result.columns_ignored.join(', ')}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="card" style={{ padding: 20 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Следующий шаг</div>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 14, lineHeight: 1.5 }}>
          После загрузки CRM-данных запустите анализ через ИИ — он объединит
          статистику Директа и заявки из CRM и предложит конкретные изменения
          в кабинете (ставки, минус-слова, объявления). Предложения появятся
          в разделе «Предложения» для проверки и одобрения.
        </div>

        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>Модель для анализа</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {providers.map(p => (
              <div
                key={p.id}
                onClick={() => p.configured && setProvider(p.id)}
                style={{
                  padding: '8px 14px',
                  borderRadius: 8,
                  border: `1px solid ${provider === p.id ? 'var(--accent)' : 'var(--border)'}`,
                  background: provider === p.id ? 'var(--accent-bg, rgba(59,130,246,0.1))' : 'var(--bg2)',
                  cursor: p.configured ? 'pointer' : 'not-allowed',
                  opacity: p.configured ? 1 : 0.4,
                  fontSize: 13,
                  fontWeight: provider === p.id ? 600 : 400,
                }}
                title={p.configured ? p.model : 'API-ключ не настроен в .env'}
              >
                {p.label} {!p.configured && '🔒'}
              </div>
            ))}
          </div>
          {providers.length > 0 && !providers.some(p => p.configured) && (
            <div style={{ marginTop: 8, fontSize: 12, color: 'var(--red)' }}>
              Ни один провайдер не настроен — добавьте хотя бы один API-ключ в .env
              (Gemini/Groq/OpenRouter — бесплатно, без карты)
            </div>
          )}
        </div>

        <button className="btn btn-primary" onClick={handleRunLLM} disabled={llmRunning || !providers.some(p => p.configured)}>
          {llmRunning ? 'Запуск...' : `🤖 Запустить ИИ-анализ (${provider})`}
        </button>
        {llmMessage && (
          <div style={{ marginTop: 10, fontSize: 13, color: 'var(--text2)' }}>{llmMessage}</div>
        )}
      </div>
    </Layout>
  )
}
