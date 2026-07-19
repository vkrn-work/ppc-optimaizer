import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

// v1.6.0: новая страница «Задачи ИИ» — свободная команда типа «добавь в рекламу
// такую-то сталь» → POST /accounts/{id}/agent-command → ИИ подбирает ключи, находит
// группу объявлений среди уже существующих и создаёт pending-предложение.
// Ничего не применяется в Директ автоматически — результат уходит на вкладку
// «Предложения» страницы «ИИ-анализ», где его нужно одобрить и применить.

export default function AiTasks() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [command, setCommand] = useState('')
  const [providers, setProviders] = useState([])
  const [provider, setProvider] = useState('claude')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [history, setHistory] = useState([])
  const [loadingHistory, setLoadingHistory] = useState(false)

  useEffect(() => {
    api.getLLMProviders()
      .then(list => {
        setProviders(list)
        const firstConfigured = list.find(p => p.configured)
        if (firstConfigured) setProvider(firstConfigured.id)
      })
      .catch(console.error)
  }, [])

  function loadHistory() {
    if (!accountId) return
    setLoadingHistory(true)
    api.getAgentCommands(accountId, 20)
      .then(rows => setHistory(Array.isArray(rows) ? rows : []))
      .catch(console.error)
      .finally(() => setLoadingHistory(false))
  }

  useEffect(() => { loadHistory() }, [accountId])

  async function handleRun() {
    const text = command.trim()
    if (!text || !accountId) return
    setRunning(true); setError(''); setResult(null)
    try {
      const res = await api.runAgentCommand(accountId, text, provider)
      setResult(res)
      setCommand('')
      loadHistory()
    } catch (e) {
      setError(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Задачи ИИ</div>
      </div>

      <div className="card" style={{ padding: 20, marginBottom: 16 }}>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 14, lineHeight: 1.5 }}>
          Опишите задачу свободным текстом — например «добавь в рекламу сталь 09Т2С толщиной 10мм» —
          и ИИ подберёт ключевые фразы, найдёт подходящую группу объявлений среди уже существующих
          и предложит минус-слова. Ничего не применяется в Директ сразу: результат уходит как
          pending-предложение на вкладку «Предложения» страницы «ИИ-анализ» — там нужно одобрить и применить.
        </div>

        <textarea
          value={command}
          onChange={e => setCommand(e.target.value)}
          placeholder="Например: добавь в рекламу трубу профильную 40х20 толщина стенки 2мм"
          rows={3}
          style={{ width: '100%', resize: 'vertical', marginBottom: 12, fontFamily: 'inherit', fontSize: 13, padding: 10 }}
        />

        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>Модель</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {providers.map(p => (
              <div key={p.id} onClick={() => p.configured && setProvider(p.id)}
                style={{
                  padding: '8px 14px', borderRadius: 8,
                  border: `1px solid ${provider === p.id ? 'var(--accent)' : 'var(--border)'}`,
                  background: provider === p.id ? 'var(--accent-bg, rgba(59,130,246,0.1))' : 'var(--bg2)',
                  cursor: p.configured ? 'pointer' : 'not-allowed',
                  opacity: p.configured ? 1 : 0.4, fontSize: 13,
                  fontWeight: provider === p.id ? 600 : 400,
                }}
                title={p.configured ? p.model : 'API-ключ не настроен в .env'}>
                {p.label} {!p.configured && '🔒'}
              </div>
            ))}
          </div>
        </div>

        <button className="btn btn-primary" onClick={handleRun} disabled={running || !command.trim() || !providers.some(p => p.configured)}>
          {running ? 'Думаю...' : '⚡ Выполнить'}
        </button>

        {error && <div style={{ marginTop: 12, color: 'var(--red)', fontSize: 13 }}>{error}</div>}

        {result && (
          <div style={{ marginTop: 16, padding: 14, borderRadius: 8, background: 'var(--bg4)', fontSize: 13 }}>
            {result.status === 'created' ? (
              <>
                <div style={{ color: 'var(--green)', fontWeight: 600, marginBottom: 6 }}>✓ Предложение создано</div>
                {result.target && (
                  <div style={{ marginBottom: 4 }}><b>Группа:</b> {typeof result.target === 'string' ? result.target : result.target.ad_group_name}</div>
                )}
                {result.keywords?.length > 0 && (
                  <div style={{ marginBottom: 4 }}><b>Ключи ({result.keywords.length}):</b> {result.keywords.join(', ')}</div>
                )}
                {result.negative_keywords?.length > 0 && (
                  <div style={{ marginBottom: 4 }}><b>Минус-слова:</b> {result.negative_keywords.join(', ')}</div>
                )}
                {result.rationale && (
                  <div style={{ marginTop: 6, color: 'var(--text2)' }}>{result.rationale}</div>
                )}
                <div style={{ marginTop: 8, color: 'var(--text3)', fontSize: 12 }}>
                  {result.message || 'Проверьте вкладку «Предложения» на странице ИИ-анализ.'}
                </div>
              </>
            ) : result.status === 'no_target' ? (
              <>
                <div style={{ color: '#e07b00', fontWeight: 600, marginBottom: 6 }}>⚠ Подходящая группа не найдена</div>
                <div style={{ color: 'var(--text2)' }}>{result.message}</div>
                {result.suggested_ad_group_name && (
                  <div style={{ marginTop: 6 }}><b>Предложенное имя группы:</b> {result.suggested_ad_group_name}</div>
                )}
              </>
            ) : (
              <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>{JSON.stringify(result, null, 2)}</pre>
            )}
          </div>
        )}
      </div>

      <div className="card" style={{ padding: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <div style={{ fontWeight: 600 }}>История команд</div>
          <button className="btn btn-sm" onClick={loadHistory}>{loadingHistory ? '⏳' : '↻'}</button>
        </div>
        {history.length === 0 ? (
          <div style={{ color: 'var(--text3)', fontSize: 13 }}>Команд ещё не было</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {history.map(h => (
              <div key={h.id} style={{ padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text3)', marginBottom: 4 }}>
                  <span>{new Date(h.created_at).toLocaleString('ru-RU')} · {h.provider}</span>
                  <span>{h.status === 'created' ? '✓ создано' : h.status === 'no_target' ? '⚠ без группы' : h.status}</span>
                </div>
                <div style={{ color: 'var(--text1)', marginBottom: 2 }}>{h.command}</div>
                {h.target_ad_group && <div style={{ color: 'var(--text2)' }}>Группа: {h.target_ad_group}</div>}
                {h.keywords?.length > 0 && <div style={{ color: 'var(--text2)' }}>Ключи: {h.keywords.join(', ')}</div>}
                {h.error && <div style={{ color: 'var(--red)' }}>{h.error}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    </Layout>
  )
}
