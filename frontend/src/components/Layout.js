import { useRouter } from 'next/router'
import Link from 'next/link'

const NAV = [
  { href: '/', label: 'Дашборд', icon: '▦' },
  { href: '/campaigns', label: 'Кампании', icon: '⊞' },
  { href: '/suggestions', label: 'Предложения', icon: '✦' },
  { href: '/keywords', label: 'Ключевые слова', icon: '⌗' },
  { href: '/hypotheses', label: 'Гипотезы', icon: '◎' },
  { href: '/rules', label: 'Правила', icon: '≋' },
  { href: '/settings', label: 'Настройки', icon: '⚙' },
]

export default function Layout({ children, account, accounts, onAccountChange }) {
  const router = useRouter()

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      {/* Sidebar */}
      <aside style={{
        width: 220,
        background: '#fff',
        borderRight: '0.5px solid var(--border-md)',
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
      }}>
        <div style={{ padding: '1.25rem', borderBottom: '0.5px solid var(--border)' }}>
          <div style={{ fontWeight: 500, fontSize: 15, marginBottom: 2 }}>PPC Optimizer</div>
          <div style={{ fontSize: 12, color: 'var(--text-3)' }}>Яндекс Директ</div>
        </div>

        {/* Account switcher */}
        {accounts && accounts.length > 0 && (
          <div style={{ padding: '0.75rem 1rem', borderBottom: '0.5px solid var(--border)' }}>
            <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>КАБИНЕТ</div>
            <select
              value={account?.id || ''}
              onChange={(e) => onAccountChange?.(Number(e.target.value))}
              style={{ width: '100%', fontSize: 12 }}
            >
              {accounts.map(a => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </div>
        )}

        {/* Navigation */}
        <nav style={{ flex: 1, padding: '0.5rem 0' }}>
          {NAV.map(item => {
            const active = router.pathname === item.href
            return (
              <Link key={item.href} href={item.href}>
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '8px 1rem',
                  fontSize: 13,
                  fontWeight: active ? 500 : 400,
                  color: active ? 'var(--text)' : 'var(--text-2)',
                  background: active ? 'var(--bg)' : 'transparent',
                  borderRadius: 6,
                  margin: '1px 8px',
                  cursor: 'pointer',
                }}>
                  <span style={{ fontSize: 14, opacity: 0.7 }}>{item.icon}</span>
                  {item.label}
                </div>
              </Link>
            )
          })}
        </nav>

        <div style={{ padding: '1rem', borderTop: '0.5px solid var(--border)', fontSize: 11, color: 'var(--text-3)' }}>
          MVP v1.0 · {new Date().getFullYear()}
        </div>
      </aside>

      {/* Main content */}
      <main style={{ flex: 1, overflow: 'auto', padding: '1.5rem 2rem', maxWidth: 1200 }}>
        {children}
      </main>
    </div>
  )
}
