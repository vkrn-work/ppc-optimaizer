import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

// v1.6.0: страница «ИИ-анализ» объединяет три раньше отдельные страницы
// (suggestions.js, crm-import.js, llm-debug.js) в вкладки одной страницы, чтобы
// весь цикл «запустить → посмотреть ответ → одобрить» был на одном экране.
// Старые URL страниц (оставлены в коде, убраны из меню) работают как раньше.

const TABS = [
  { key: 'suggestions', label: 'Предложения' },
  { key: 'run',         label: 'Запустить анализ' },
  { key: 'debug',       label: 'История вход/выход' },
]

export default function AiAnalysis() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [tab, setTab] = useState('suggestions')

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">ИИ-анализ</div>
        <div className="period-tabs">
          {TABS.map(t => (
            <div key={t.key} className={`period-tab${tab === t.key ? ' active' : ''}`}
              onClick={() => setTab(t.key)}>{t.label}</div>
          ))}
        </div>
      </div>

      <div style={{ display: tab === 'suggestions' ? 'block' : 'none' }}>
        <SuggestionsSection accountId={accountId} />
      </div>
      <div style={{ display: tab === 'run' ? 'block' : 'none' }}>
        <RunAnalysisSection accountId={accountId} />
      </div>
      <div style={{ display: tab === 'debug' ? 'block' : 'none' }}>
        <DebugSection accountId={accountId} />
      </div>
    </Layout>
  )
}

// ============================================================
// Вкладка 1: Предложения (порт suggestions.js)
// ============================================================

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
  add_keywords:   '🔑 Новые ключи',
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

function fR(n) { return n == null ? '—' : Math.round(n).toLocaleString('ru') + ' ₽' }

function SuggestionsSection({ accountId }) {
  const [status, setStatus]   = useState('pending')
  const [items, setItems]     = useState([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy]       = useState({})
  const [expanded, setExpanded] = useState(new Set())
  const [filters, setFilters] = useState({ priority: '', changeType: '', search: '' })

  function load() {
    if (!accountId) return
    setLoading(true)
    api.getSuggestions(accountId, `?status=${status}`)
      .then(rows => setItems(Array.isArray(rows) ? rows : []))
      .catch(console.error)
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [accountId, status])

  const setF = (k, v) => setFilters(f => ({ ...f, [k]: v }))

  const filtered = items.filter(item => {
    if (filters.priority && item.priority !== filters.priority) return false
    if (filters.changeType && item.change_type !== filters.changeType) return false
    if (filters.search) {
      const q = filters.search.toLowerCase()
      const txt = (item.phrase || '') + ' ' + (item.description || '') + ' ' + (item.action || '')
      if (!txt.toLowerCase().includes(q)) return false
    }
    return true
  })

  const changeTypesInData = [...new Set(items.map(i => i.change_type).filter(Boolean))]

  const byPriority = {
    today:     filtered.filter(i => i.priority === 'today'),
    this_week: filtered.filter(i => i.priority === 'this_week'),
    month:     filtered.filter(i => i.priority === 'month'),
    scale:     filtered.filter(i => i.priority === 'scale' || !i.priority),
  }

  async function approve(item) {
    setBusy(b => ({ ...b, [item.id]: true }))
    try {
      await api.actionSuggestion(item.id, { action: 'accept' })
      setItems(prev => prev.filter(x => x.id !== item.id))
    } catch (e) {
      alert('Ошибка: ' + e.message)
    } finally {
      setBusy(b => ({ ...b, [item.id]: false }))
    }
  }

  async function reject(item) {
    const reason = window.prompt('Причина отклонения (необязательно):') || undefined
    setBusy(b => ({ ...b, [item.id]: true }))
    try {
      await api.actionSuggestion(item.id, { action: 'reject', reason })
      setItems(prev => prev.filter(x => x.id !== item.id))
    } catch (e) {
      alert('Ошибка: ' + e.message)
    } finally {
      setBusy(b => ({ ...b, [item.id]: false }))
    }
  }

  async function applyToDirect(item) {
    if (!window.confirm(`Применить изменение в кабинете Директа?\n\n${item.phrase}\n${item.value_before} → ${item.value_after}`)) return
    setBusy(b => ({ ...b, [item.id]: true }))
    try {
      const res = await api.applySuggestion(item.id)
      if (res.status === 'applied') {
        setItems(prev => prev.map(x => x.id === item.id ? { ...x, status: 'applied' } : x))
        alert('Применено в кабинете Директа: ' + (res.detail || 'OK'))
      } else {
        alert('Не применилось: ' + (res.detail || JSON.stringify(res)))
      }
    } catch (e) {
      alert('Ошибка применения: ' + e.message)
    } finally {
      setBusy(b => ({ ...b, [item.id]: false }))
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
    const borderColor = sev === 'critical' ? 'var(--red)' : sev === 'warning' ? '#e07b00' : sev === 'info' ? 'var(--accent)' : 'var(--green)'

    return (
      <div style={{
        border: `1px solid var(--border)`,
        borderLeft: `3px solid ${borderColor}`,
        borderRadius: 8,
        padding: '12px 14px',
        background: 'var(--bg2)',
        cursor: 'pointer',
      }} onClick={() => toggleExpanded(item.id)}>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 4, alignItems: 'center' }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: borderColor }}>
                {CHANGE_TYPE_LABELS[item.change_type] || item.change_type || '—'}
              </span>
              {item.priority && (
                <span style={{ fontSize: 10, color: 'var(--text3)' }}>{PRI_LABELS[item.priority]}</span>
              )}
              {item.status && item.status !== 'pending' && (
                <span style={{ fontSize: 10, background: 'var(--bg4)', color: 'var(--text3)', padding: '1px 5px', borderRadius: 3 }}>
                  {item.status}
                </span>
              )}
            </div>

            <div style={{ fontFamily: 'monospace', fontSize: 11, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%', color: 'var(--text1)', marginBottom: 4 }} title={item.phrase}>
              {item.phrase || '—'}
            </div>

            <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 2 }}>
              {item.description}
            </div>

            {(item.value_before || item.value_after) && (
              <div style={{ fontSize: 12, color: 'var(--accent)', fontWeight: 500 }}>
                {item.value_before || '—'} → {item.value_after || '—'}
              </div>
            )}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flexShrink: 0, marginTop: 2 }} onClick={e => e.stopPropagation()}>
            {item.status === 'pending' && (
              <>
                <button className="btn btn-sm btn-primary" onClick={() => approve(item)} disabled={isBusy}>
                  {isBusy ? '⏳' : '✓ Одобрить'}
                </button>
                <button className="btn btn-sm" onClick={() => reject(item)} disabled={isBusy}>
                  ✕ Отклонить
                </button>
              </>
            )}
            {item.status === 'approved' && (
              <button className="btn btn-sm btn-primary" onClick={() => applyToDirect(item)} disabled={isBusy}>
                {isBusy ? '⏳' : '🚀 Применить в Директе'}
              </button>
            )}
            {item.status === 'applied' && (
              <span style={{ fontSize: 11, color: 'var(--green)' }}>✓ Применено</span>
            )}
            {item.status === 'rejected' && (
              <span style={{ fontSize: 11, color: 'var(--text3)' }}>✕ Отклонено</span>
            )}
          </div>
        </div>

        {isOpen && (
          <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }} onClick={e => e.stopPropagation()}>
            {item.rationale && (
              <div style={{ fontSize: 12, marginBottom: 6 }}>
                <span style={{ color: 'var(--text3)', fontWeight: 500 }}>Обоснование: </span>
                <span style={{ color: 'var(--text2)' }}>{item.rationale}</span>
              </div>
            )}
            {item.expected_effect && (
              <div style={{ fontSize: 12, marginBottom: 6 }}>
                <span style={{ color: 'var(--text3)', fontWeight: 500 }}>Ожидаем: </span>
                <span style={{ color: 'var(--text2)' }}>{item.expected_effect}</span>
              </div>
            )}
            {item.recommended_bid != null && (
              <div style={{ fontSize: 12, marginBottom: 6 }}>
                <span style={{ color: 'var(--text3)', fontWeight: 500 }}>Рекомендованная ставка: </span>
                <span style={{ color: 'var(--text1)' }}>{fR(item.recommended_bid)}</span>
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  const anyFilters = filters.priority || filters.changeType || filters.search

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap', alignItems: 'center' }}>
        <div className="period-tabs">
          {STATUS_TABS.map(t => (
            <div key={t.key} className={`period-tab${status === t.key ? ' active' : ''}`}
              onClick={() => setStatus(t.key)}>{t.label}</div>
          ))}
        </div>

        <input placeholder="Поиск по ключу или описанию..." value={filters.search}
          onChange={e => setF('search', e.target.value)} style={{ width: 220 }} />

        <select value={filters.priority} onChange={e => setF('priority', e.target.value)}
          className="btn" style={{ padding: '5px 10px' }}>
          <option value="">Все приоритеты</option>
          <option value="today">🔴 Сегодня</option>
          <option value="this_week">🟡 Эта неделя</option>
          <option value="month">🔵 Месяц</option>
          <option value="scale">🟢 Масштаб</option>
        </select>

        <select value={filters.changeType} onChange={e => setF('changeType', e.target.value)}
          className="btn" style={{ padding: '5px 10px' }}>
          <option value="">Все типы изменений</option>
          {changeTypesInData.map(t => (
            <option key={t} value={t}>{CHANGE_TYPE_LABELS[t] || t}</option>
          ))}
        </select>

        {anyFilters && (
          <button className="btn" onClick={() => setFilters({ priority: '', changeType: '', search: '' })}>
            × Сбросить
          </button>
        )}

        <span style={{ fontSize: 11, color: 'var(--text3)', marginLeft: 'auto' }}>
          {filtered.length} из {items.length}
        </span>
      </div>

      {loading ? (
        <div style={{ color: 'var(--text3)', fontSize: 13, padding: '2rem', textAlign: 'center' }}>
          Загрузка...
        </div>
      ) : items.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-icon">◈</div>
            <div className="empty-title">
              {status === 'pending' ? 'Нет предложений, ожидающих решения' : 'Пусто'}
            </div>
            <div className="empty-desc">
              {status === 'pending'
                ? 'Запустите ИИ-анализ на вкладке «Запустить анализ» или на странице «Задачи ИИ»'
                : 'В этом статусе пока ничего нет'}
            </div>
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text3)' }}>
          Нет предложений по выбранным фильтрам
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {[
            { key: 'today',     items: byPriority.today },
            { key: 'this_week', items: byPriority.this_week },
            { key: 'month',     items: byPriority.month },
            { key: 'scale',     items: byPriority.scale },
          ].filter(g => g.items.length > 0).map(group => (
            <div key={group.key}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--text2)' }}>
                {PRI_LABELS[group.key]} — {group.items.length} {group.items.length === 1 ? 'предложение' : 'предложений'}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {group.items.map(item => (
                  <SuggestionCard key={item.id} item={item} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

// ============================================================
// Вкладка 2: Запустить анализ (порт crm-import.js)
// ============================================================

function RunAnalysisSection({ accountId }) {
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [llmRunning, setLlmRunning] = useState(false)
  const [llmMessage, setLlmMessage] = useState('')
  const [providers, setProviders] = useState([])
  const [provider, setProvider] = useState('claude')

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
      setLlmMessage(res.message || 'Анализ запущен. Проверьте вкладку «Предложения» через 1-2 минуты.')
    } catch (e) {
      setLlmMessage('Ошибка: ' + e.message)
    } finally {
      setLlmRunning(false)
    }
  }

  return (
    <>
      <div className="card" style={{ padding: 20, marginBottom: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>Загрузка выгрузки из CRM (необязательно)</div>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 14, lineHeight: 1.5 }}>
          Загрузите CSV или XLSX-файл с заявками из вашей CRM — это улучшает качество анализа,
          но не обязательно: АПИ может работать и на одной статистике Директа/Метрики.
        </div>

        <form onSubmit={handleUpload}>
          <input
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
            <div>✓ Импортировано заявок: <b>{result.imported}</b> из {result.total_rows} строк в файле</div>
            <div>Воронка: MQL — <b>{result.mql_count}</b>, SQL — <b>{result.sql_count}</b></div>
            <div style={{ marginTop: 6 }}>Сопоставлено по номеру объявления: <b>{result.matched_by_ad_id}</b></div>
            <div>Сопоставлено по фразе: <b>{result.matched_by_phrase}</b></div>
            <div>Не удалось сопоставить: <b>{result.unmatched}</b></div>
          </div>
        )}
      </div>

      <div className="card" style={{ padding: 20 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Запустить ИИ-анализ</div>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 14, lineHeight: 1.5 }}>
          ИИ объединит статистику Директа и (если есть) заявки из CRM и предложит конкретные
          изменения в кабинете. Предложения появятся на вкладке «Предложения» для проверки и одобрения.
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
    </>
  )
}

// ============================================================
// Вкладка 3: История вход/выход (порт llm-debug.js)
// ============================================================

function DebugSection({ accountId }) {
  const [analyses, setAnalyses] = useState([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState(null)
  const [showRaw, setShowRaw] = useState(false)

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
  const diagnostics = Array.isArray(s.llm_diagnostics) ? s.llm_diagnostics : []
  const execSummary = s.llm_executive_summary || ''
  const changesCount = s.llm_raw_output?.length ?? 0
  const hasChat = diagnostics.length > 0 || execSummary

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <div style={{ fontSize: 13, color: 'var(--text2)', maxWidth: 700 }}>
          Здесь видно ход мыслей ИИ по каждому запуску анализа — что проверял, что нашёл и почему предлагает именно это.
        </div>
        <button className="btn" onClick={load}>↻ Обновить</button>
      </div>

      {loading ? (
        <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text3)' }}>Загрузка...</div>
      ) : analyses.length === 0 ? (
        <div className="card" style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>
          Ещё не было ни одного ИИ-анализа. Запустите его на вкладке «Запустить анализ».
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
              <div><b>Изменений от ИИ:</b> {changesCount}</div>
              <div><b>Прошло в suggestions:</b> {s.suggestions_created ?? '—'}</div>
              <div><b>Отклонено safety-лимитами:</b> {s.rejected_by_safety_limits ?? '—'}</div>
            </div>
            {s.error && (
              <div style={{ marginTop: 10, color: 'var(--red)', fontSize: 13 }}>
                Ошибка вызова LLM: {s.error}
              </div>
            )}
          </div>

          {/* v1.7.1: живой «чат» с ИИ — пошаговый рассказ о ходе анализа человеческим
              языком вместо голого JSON. diagnostics/summary приходят из того же вызова
              LLM, что и changes — модель сама объясняет, что проверяла и почему. */}
          <div className="card" style={{ padding: 18, marginBottom: 14 }}>
            <div style={{ fontWeight: 600, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
              🤖 Разбор ИИ-аналитика
            </div>

            {!hasChat ? (
              <div style={{ fontSize: 13, color: 'var(--text3)' }}>
                {s.error
                  ? 'Анализ завершился ошибкой до получения ответа модели — рассказа нет, см. ошибку выше.'
                  : 'Этот запуск сделан до обновления с пошаговым разбором — здесь только сырые данные ниже. Запустите анализ заново, чтобы увидеть рассказ ИИ.'}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{
                  alignSelf: 'center', fontSize: 12, color: 'var(--text3)', fontStyle: 'italic',
                  textAlign: 'center', marginBottom: 4,
                }}>
                  — Запуск анализа: {s.llm_input_full_count ?? '?'} ключевых слов, провайдер {s.provider} ({s.model}) —
                </div>

                {diagnostics.map((step, i) => (
                  <div key={i} style={{
                    alignSelf: 'flex-start', maxWidth: '85%',
                    background: 'var(--bg4)', borderRadius: '14px 14px 14px 4px',
                    padding: '10px 14px', fontSize: 13, lineHeight: 1.6, color: 'var(--text1)',
                  }}>
                    {step}
                  </div>
                ))}

                {execSummary && (
                  <div style={{
                    alignSelf: 'flex-start', maxWidth: '90%',
                    background: 'var(--accent-bg, rgba(59,130,246,0.1))',
                    border: '1px solid var(--accent)',
                    borderRadius: '14px 14px 14px 4px',
                    padding: '10px 14px', fontSize: 13, lineHeight: 1.6, fontWeight: 500,
                  }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400, marginBottom: 4 }}>Итог</div>
                    {execSummary}
                  </div>
                )}

                <div style={{ alignSelf: 'center', fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>
                  {changesCount} предложений сформировано · {s.suggestions_created ?? 0} прошло проверку лимитов
                  {(s.rejected_by_safety_limits ?? 0) > 0 && <> · {s.rejected_by_safety_limits} отклонено safety-лимитами</>}
                  {' '}— см. вкладку «Предложения».
                </div>
              </div>
            )}
          </div>

          <div style={{ marginBottom: 10 }}>
            <button className="btn" onClick={() => setShowRaw(v => !v)}>
              {showRaw ? '▾ Скрыть технические данные' : '▸ Показать технические данные (сырой JSON)'}
            </button>
          </div>

          {showRaw && (
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

              {/* v1.7.2: агрегированный контекст аккаунта, который уходит в модель
                  вместе с построчными данными — бенчмарки, разрез по кампаниям,
                  длинный хвост, сырой поисковый спрос. */}
              {s.llm_input_context && (
                <div className="card" style={{ padding: 16, gridColumn: '1 / -1' }}>
                  <div style={{ fontWeight: 600, marginBottom: 8 }}>
                    📊 Контекст аккаунта, отправленный в модель (бенчмарки, кампании, спрос)
                  </div>
                  <pre style={{
                    fontSize: 11, background: 'var(--bg4)', padding: 10, borderRadius: 6,
                    overflow: 'auto', maxHeight: 400, whiteSpace: 'pre-wrap',
                  }}>
                    {JSON.stringify(s.llm_input_context, null, 2)}
                  </pre>
                </div>
              )}

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
          )}
        </>
      )}
    </>
  )
}
