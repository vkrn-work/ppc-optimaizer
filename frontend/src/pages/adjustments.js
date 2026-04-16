import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'

const BASE = 'https://ppc-optimaizer-production.up.railway.app'

function pct(n) { return !n ? '—' : (Math.round(n * 10) / 10) + '%' }
function num(n) { return (!n && n !== 0) ? '—' : Math.round(n) }
function sec(n) { return !n ? '—' : Math.round(n) + 'с' }

const TABS = ['Устройства', 'Регионы', 'Время', 'Дни недели']
const WEEKDAYS = ['', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

export default function Adjustments() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [tab, setTab] = useState('Устройства')
  const [metrika, setMetrika] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!accountId) return
    setLoading(true)
    fetch(`${BASE}/api/v1/accounts/${accountId}/metrika-snapshot`)
      .then(r => r.json())
      .then(setMetrika)
      .catch(() => setMetrika(null))
      .finally(() => setLoading(false))
  }, [accountId])

  const data = metrika?.data || {}

  function renderDevices() {
    const rows = data.devices || []
    if (!rows.length) return <Empty />
    return (
      <table>
        <thead>
          <tr>
            <th>Устройство</th>
            <th>Визиты</th>
            <th>Отказы</th>
            <th>Время</th>
            <th>Рекомендация</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const bounce = r.bounceRate || 0
            const rec = bounce > 70 ? { text: `-50% корректировка`, cls: 'badge-bad' }
              : bounce < 20 ? { text: `+20% корректировка`, cls: 'badge-ok' }
              : null
            return (
              <tr key={i}>
                <td style={{ fontWeight: 500 }}>
                  {r.deviceCategory === 'desktop' ? '🖥 Десктоп'
                    : r.deviceCategory === 'mobile' ? '📱 Мобильный'
                    : r.deviceCategory === 'tablet' ? '📟 Планшет'
                    : r.deviceCategory || '—'}
                </td>
                <td>{num(r.visits)}</td>
                <td>
                  <span style={{ color: bounce > 60 ? 'var(--red)' : bounce > 40 ? 'var(--yellow)' : 'var(--green)' }}>
                    {pct(bounce)}
                  </span>
                </td>
                <td>{sec(r.avgVisitDurationSeconds)}</td>
                <td>{rec ? <span className={`badge ${rec.cls}`}>{rec.text}</span> : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    )
  }

  function renderRegions() {
    const rows = data.regions || []
    if (!rows.length) return <Empty msg="Регионы не собраны. Запустите сбор данных." />
    return (
      <table>
        <thead>
          <tr><th>Город</th><th>Визиты</th><th>Отказы</th><th>Время</th></tr>
        </thead>
        <tbody>
          {rows.slice(0, 30).map((r, i) => (
            <tr key={i}>
              <td style={{ fontWeight: 500 }}>{r.regionCity || '—'}</td>
              <td>{num(r.visits)}</td>
              <td style={{ color: (r.bounceRate || 0) > 60 ? 'var(--red)' : 'inherit' }}>{pct(r.bounceRate)}</td>
              <td>{sec(r.avgVisitDurationSeconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  function renderTime() {
    const rows = data.by_hour || []
    if (!rows.length) return <Empty msg="Данные по часам не собраны." />
    const maxVisits = Math.max(...rows.map(r => r.visits || 0))
    return (
      <div>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 80, marginBottom: 8 }}>
          {Array.from({ length: 24 }, (_, h) => {
            const row = rows.find(r => Number(r.hourOfDay) === h)
            const v = row?.visits || 0
            const height = maxVisits > 0 ? Math.max(4, (v / maxVisits) * 70) : 4
            return (
              <div key={h} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                <div style={{ width: '100%', height, background: 'var(--accent)', borderRadius: 2, opacity: v > 0 ? 1 : 0.2 }} />
              </div>
            )
          })}
        </div>
        <div style={{ display: 'flex', fontSize: 10, color: 'var(--text3)' }}>
          {[0,3,6,9,12,15,18,21].map(h => (
            <div key={h} style={{ flex: '3 1 0', textAlign: 'center' }}>{h}:00</div>
          ))}
        </div>
        <table style={{ marginTop: 12 }}>
          <thead><tr><th>Час</th><th>Визиты</th><th>Отказы</th></tr></thead>
          <tbody>
            {rows.sort((a,b) => (b.visits||0)-(a.visits||0)).slice(0,10).map((r,i) => (
              <tr key={i}>
                <td>{r.hourOfDay}:00</td>
                <td>{num(r.visits)}</td>
                <td>{pct(r.bounceRate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  function renderWeekdays() {
    const rows = data.by_weekday || []
    if (!rows.length) return <Empty />
    const maxV = Math.max(...rows.map(r => r.visits || 0))
    return (
      <div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
          {rows.sort((a,b) => Number(a.dayOfWeek)-Number(b.dayOfWeek)).map((r,i) => {
            const v = r.visits || 0
            const height = maxV > 0 ? Math.max(20, (v / maxV) * 100) : 20
            return (
              <div key={i} style={{ flex: 1, textAlign: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'center', height: 100 }}>
                  <div style={{ width: '60%', height, background: 'var(--accent)', borderRadius: 4, opacity: 0.8 }} />
                </div>
                <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 4 }}>{WEEKDAYS[r.dayOfWeek] || r.dayOfWeek}</div>
                <div style={{ fontSize: 12, fontWeight: 600 }}>{num(v)}</div>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div className="page-header">
        <div className="page-title">Корректировки</div>
      </div>
      <div className="period-tabs" style={{ marginBottom: 14, display: 'inline-flex' }}>
        {TABS.map(t => (
          <div key={t} className={`period-tab${tab === t ? ' active' : ''}`} onClick={() => setTab(t)}>{t}</div>
        ))}
      </div>
      <div className="card" style={{ padding: tab === 'Время' || tab === 'Дни недели' ? 16 : 0, overflow: 'auto' }}>
        {loading ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text3)' }}>Загрузка...</div>
        ) : tab === 'Устройства' ? renderDevices()
          : tab === 'Регионы' ? renderRegions()
          : tab === 'Время' ? renderTime()
          : renderWeekdays()}
      </div>
    </Layout>
  )
}

function Empty({ msg = 'Данные появятся после следующего сбора' }) {
  return (
    <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>{msg}</div>
  )
}
