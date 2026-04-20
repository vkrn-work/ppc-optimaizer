import { useState, useEffect } from 'react'
import { useRouter } from 'next/router'
import { api } from '../utils/api'

const NAV = [
  { section: 'АНАЛИЗ', items: [
    { href: '/',            icon: '◉', label: 'Main Board' },
    { href: '/campaigns',   icon: '≡', label: 'По кампаниям' },
    { href: '/bids',        icon: '₽', label: 'Ставки' },
    { href: '/adjustments', icon: '⊕', label: 'Корректировки' },
  ]},
  { section: 'ПОИСКОВЫЕ ФРАЗЫ', items: [
    { href: '/new-keywords', icon: '+', label: 'Новые ключи',  badgeKey: 'new_kw',  badgeColor: 'green' },
    { href: '/negatives',    icon: '×', label: 'Минуса',       badgeKey: 'neg' },
  ]},
  { section: 'ОПТИМИЗАЦИЯ', items: [
    { href: '/suggestions', icon: '◈', label: 'Предложения',  badgeKey: 'suggest', badgeColor: 'accent' },
    { href: '/hypotheses',  icon: '◇', label: 'Гипотезы' },
  ]},
  { section: 'СИСТЕМА', items: [
    { href: '/settings',    icon: '⊙', label: 'Кабинеты' },
    { href: '/rules',       icon: '≋', label: 'Правила' },
    { href: '/diagnostics', icon: '⚠', label: 'Диагностика',  badgeKey: 'errors',  danger: true },
  ]},
]

function getMSK() {
  return new Date().toLocaleTimeString('ru-RU', {
    timeZone: 'Europe/Moscow',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// Форматировать UTC-дату из БД как МСК
// last_sync_at приходит без Z — добавляем чтобы браузер трактовал как UTC
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
    fetch(`https://ppc-optimaizer-production.up.railway.app/api/v1/accounts/${accountId}/analyses`)
      .then(r => r.json())
      .then(data => {
        const a = data?.[0]
        const urgentProblems = (a?.problems || []).filter(p => p.priority === 'today').length
        setBadges(prev => ({ ...prev, suggest: urgentProblems || null }))
      }).catch(() => {})
    fetch(`https://ppc-optimaizer-production.up.railway.app/health`)
      .then(r => r.json())
      .then(h => {
        if (!account?.oauth_token || !account?.metrika_counter_id) {
          setBadges(prev => ({ ...prev, errors: 1 }))
        }
      }).catch(() => setBadges(prev => ({ ...prev, errors: 1 })))
  }, [accountId, account])

  async function handleSync() {
    if (!accountId || syncing) return
    setSyncing(true)
    try { await api.triggerSync(accountId) } catch (e) {}
    finally { setTimeout(() => setSyncing(false), 2000) }
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
          <button className="btn btn-sm btn-primary" onClick={handleSync} disabled={syncing} style={{ minWidth: 120 }}>
            {syncing ? '⏳ Запуск...' : '↻ Обновить данные'}
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
