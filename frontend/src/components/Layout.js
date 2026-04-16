import { useState, useEffect } from 'react'
import { useRouter } from 'next/router'
import { api } from '../utils/api'

const NAV = [
  { section: 'АНАЛИЗ', items: [
    { href: '/',           icon: '◉', label: 'Main Board' },
    { href: '/campaigns',  icon: '≡', label: 'По кампаниям' },
    { href: '/bids',       icon: '₽', label: 'Ставки' },
    { href: '/adjustments',icon: '⊕', label: 'Корректировки' },
  ]},
  { section: 'ПОИСКОВЫЕ ФРАЗЫ', items: [
    { href: '/new-keywords', icon: '+', label: 'Новые ключи', badgeKey: 'new_keywords', badgeColor: 'green' },
    { href: '/negatives',    icon: '×', label: 'Минуса',      badgeKey: 'negatives' },
  ]},
  { section: 'ОПТИМИЗАЦИЯ', items: [
    { href: '/suggestions', icon: '◈', label: 'Предложения', badgeKey: 'suggestions' },
    { href: '/hypotheses',  icon: '◇', label: 'Гипотезы' },
  ]},
  { section: 'СИСТЕМА', items: [
    { href: '/settings',     icon: '⊙', label: 'Кабинеты' },
    { href: '/rules',        icon: '≋', label: 'Правила' },
    { href: '/diagnostics',  icon: '⚠', label: 'Диагностика', badgeKey: 'errors', danger: true },
  ]},
]

function getMSKTime() {
  return new Date().toLocaleTimeString('ru-RU', {
    timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit'
  })
}

export default function Layout({ children, account, accounts, onAccountChange }) {
  const router = useRouter()
  const [collapsed, setCollapsed] = useState(false)
  const [theme, setTheme] = useState('dark')
  const [time, setTime] = useState(getMSKTime())
  const [syncing, setSyncing] = useState(false)
  const [badges, setBadges] = useState({})
  const accountId = account?.id

  useEffect(() => {
    const t = setInterval(() => setTime(getMSKTime()), 30000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  useEffect(() => {
    if (!accountId) return
    // Загрузить счётчики для бейджей
    api.getSuggestions(accountId, '?status=pending').then(d => {
      const urgent = d.filter ? d.filter(s => s.priority === 'today').length : 0
      setBadges(prev => ({ ...prev, suggestions: urgent || null }))
    }).catch(() => {})
  }, [accountId])

  async function handleSync() {
    if (!accountId || syncing) return
    setSyncing(true)
    try { await api.triggerSync(accountId) } catch (e) {}
    finally { setTimeout(() => setSyncing(false), 2000) }
  }

  const lastSync = account?.last_sync_at
    ? new Date(account.last_sync_at).toLocaleString('ru-RU', {
        timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit',
        hour: '2-digit', minute: '2-digit'
      }) + ' МСК'
    : null

  return (
    <>
      {/* TOPBAR */}
      <div className="app-topbar">
        <div className="topbar-logo">PPC <span>Optimizer</span></div>
        <div className="topbar-sep" />
        <div className="topbar-status">
          <div className="status-dot" />
          {lastSync ? `Обновлено: ${lastSync}` : `Сейчас: ${time} МСК`}
        </div>
        <div className="topbar-right">
          <button
            className="btn btn-sm"
            onClick={handleSync}
            disabled={syncing}
            style={{ minWidth: 110 }}
          >
            {syncing ? '⏳ Запуск...' : '↻ Обновить данные'}
          </button>
          <div
            className="sb-toggle"
            onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
            title="Переключить тему"
          >
            {theme === 'dark' ? '☀' : '☾'}
          </div>
        </div>
      </div>

      {/* SIDEBAR */}
      <div className={`app-sidebar${collapsed ? ' collapsed' : ''}`}>
        {/* Кабинет */}
        <div className="sb-cabinet">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: collapsed ? 0 : 6 }}>
            {!collapsed && <div className="sb-label" style={{ padding: 0 }}>Кабинет</div>}
            <div className="sb-toggle" onClick={() => setCollapsed(c => !c)} style={{ marginLeft: collapsed ? 'auto' : 0 }}>
              {collapsed ? '›' : '‹'}
            </div>
          </div>
          {!collapsed && (
            <select
              className="cabinet-select"
              value={accountId || ''}
              onChange={e => onAccountChange && onAccountChange(Number(e.target.value))}
            >
              {accounts?.map(a => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          )}
        </div>

        {/* Navigation */}
        {NAV.map(({ section, items }) => (
          <div key={section} className="sb-section">
            <div className="sb-label">{section}</div>
            {items.map(item => {
              const isActive = router.pathname === item.href
              const badgeVal = badges[item.badgeKey]
              return (
                <div
                  key={item.href}
                  className={`sb-item${isActive ? ' active' : ''}${item.danger ? ' danger' : ''}`}
                  onClick={() => router.push(item.href)}
                  style={item.danger ? { color: 'var(--red)' } : {}}
                  title={collapsed ? item.label : ''}
                >
                  <span className="sb-icon">{item.icon}</span>
                  <span className="sb-text">{item.label}</span>
                  {badgeVal && (
                    <span className={`sb-badge${item.badgeColor ? ' ' + item.badgeColor : ''}`}>
                      {badgeVal}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        ))}
      </div>

      {/* MAIN */}
      <div className={`app-main${collapsed ? ' expanded' : ''}`}>
        {children}
      </div>
    </>
  )
}
