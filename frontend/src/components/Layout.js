import { useState, useEffect } from 'react'
import { useRouter } from 'next/router'
import { api } from '../utils/api'

// v1.6.0: новая структура навигации — 5 разделов вместо 13 разрозненных страниц.
// Старые страницы (Новые ключи, Минуса, Правила, Гипотезы) оставлены в коде,
// но убраны из меню по решению пользователя.
const NAV = [
  { section: 'ГЛАВНАЯ', items: [
    { href: '/', icon: '◉', label: 'Главная' },
  ]},
  { section: 'АНАЛИЗ', items: [
    { href: '/analysis', icon: '≡', label: 'Полный анализ' },
  ]},
  { section: 'ИИ', items: [
    { href: '/ai-analysis', icon: '🤖', label: 'ИИ-анализ', badgeKey: 'suggest', badgeColor: 'accent' },
    { href: '/ai-tasks',    icon: '⚡', label: 'Задачи ИИ' },
  ]},
  { section: 'СИСТЕМА', items: [
    { href: '/settings',    icon: '⊙', label: 'Кабинеты' },
    { href: '/diagnostics', icon: '⚠', label: 'Диагностика', badgeKey: 'errors', danger: true },
  ]},
]

function getMSK() {
  return new Date().toLocaleTimeString('ru-RU', {
    timeZone: 'Europe/Moscow',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatSyncTime(isoString) {
  if (!isoString) return null
  const s = isoString.endsWith('Z') ? isoString : isoString + 'Z'
  return new Date(s).toLocaleString('ru-RU', {
    timeZone: 'Europe/Moscow',
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function Layout({ children, account, accounts, onAccountChange }) {
  const router = useRouter()
  const [collapsed, setCollapsed] = useState(false)
  const [theme, setTheme] = useState(() => {
    if (typeof window !== 'undefined') return localStorage.getItem('theme') || 'light'
    return 'light'
  })
  const [time, setTime] = useState(getMSK())
  const [syncing, setSyncing] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [badges, setBadges] = useState({})
  const accountId = account?.id

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    if (typeof window !== 'undefined') localStorage.setItem('theme', theme)
  }, [theme])

  useEffect(() => {
    const t = setInterval(() => setTime(getMSK()), 30000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (!accountId) return
    // CHANGED v1.6.0: badge "suggest" раньше читал analysis.problems (мёртвый
    // rule-based формат, который LLM-анализатор не заполняет) — теперь берём
    // реальное число pending-предложений из таблицы suggestions.
    fetch(`https://ppc-optimaizer-production.up.railway.app/api/v1/accounts/${accountId}/suggestions?status=pending`)
      .then(r => r.json())
      .then(rows => {
        const n = Array.isArray(rows) ? rows.length : 0
        setBadges(prev => ({ ...prev, suggest: n || null }))
      }).catch(() => {})
    fetch(`https://ppc-optimaizer-production.up.railway.app/api/v1/health`)
      .then(r => r.json())
      .then(h => {
        if (!account?.oauth_token || !account?.metrika_counter_id) {
          setBadges(prev => ({ ...prev, errors: 1 }))
        }
      }).catch(() => setBadges(prev => ({ ...prev, errors: 1 })))
  }, [accountId, account])

  async function handleSync() {
    if (!accountId || syncing || analyzing) return
    setSyncing(true)
    try { await api.triggerSync(accountId) } catch (e) {}
    finally { setTimeout(() => setSyncing(false), 2000) }
  }

  async function handleAnalyze() {
    if (!accountId || syncing || analyzing) return
    setAnalyzing(true)
    try {
      await api.runAnalysis(accountId)
      alert('✅ Анализ завершен на текущих данных')
    } catch (e) {
      alert('❌ Ошибка запуска анализа')
    } finally {
      setTimeout(() => setAnalyzing(false), 1500)
    }
  }

  const syncFormatted = formatSyncTime(account?.last_sync_at)
  const lastSync = syncFormatted
    ? `Обновлено: ${syncFormatted} МСК`
    : `Сейчас: ${time} МСК`

  const isDark = theme === 'dark'

  return (
    <>
      {/* ── TOPBAR ── */}
      <div className="app-topbar">
        <div className="topbar-logo">PPC <span>Optimizer</span></div>
        <div className="topbar-sep" />
        <div className="topbar-status">
          <div className="status-dot" />
          {lastSync}
        </div>
        <div className="topbar-right">
          <button 
            className="btn btn-sm btn-primary" 
            onClick={handleSync} 
            disabled={syncing || analyzing}
            style={{ minWidth: 130 }}
          >
            {syncing ? '⏳ Запуск...' : '↻ Обновить данные'}
          </button>
          <button 
            className="btn btn-sm btn-success" 
            onClick={handleAnalyze} 
            disabled={syncing || analyzing}
            style={{ minWidth: 130 }}
          >
            {analyzing ? '🧠 Анализ...' : '🧠 Только анализ'}
          </button>
          <div className="sb-toggle" onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
            title={isDark ? 'Светлая тема' : 'Тёмная тема'} style={{ fontSize: 14 }}>
            {isDark ? '☀' : '☾'}
          </div>
        </div>
      </div>

      {/* ── SIDEBAR ── */}
      <div className={`app-sidebar${collapsed ? ' collapsed' : ''}`}>
        <div className="sb-cabinet">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
            {!collapsed && (
              <select className="cabinet-select" value={accountId || ''}
                onChange={e => onAccountChange && onAccountChange(Number(e.target.value))}>
                {accounts?.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            )}
            <div className="sb-toggle" onClick={() => setCollapsed(c => !c)} style={{ flexShrink: 0 }}>
              {collapsed ? '›' : '‹'}
            </div>
          </div>
        </div>

        {NAV.map(({ section, items }) => (
          <div key={section} className="sb-section">
            <div className="sb-label">{section}</div>
            {items.map(item => {
              const active = router.pathname === item.href
              const bv = badges[item.badgeKey]
              return (
                <div key={item.href}
                  className={`sb-item${active ? ' active' : ''}${item.danger ? ' sb-danger' : ''}`}
                  onClick={() => router.push(item.href)}
                  title={collapsed ? item.label : ''}
                  style={item.danger ? { color: 'var(--red)' } : {}}
                >
                  <span className="sb-icon">{item.icon}</span>
                  <span className="sb-text">{item.label}</span>
                  {bv != null && (
                    <span className={`sb-badge${item.badgeColor ? ' ' + item.badgeColor : ''}`}>{bv}</span>
                  )}
                </div>
              )
            })}
          </div>
        ))}
      </div>

      {/* ── MAIN ── */}
      <div className={`app-main${collapsed ? ' expanded' : ''}`}>
        {children}
      </div>
    </>
  )
}
