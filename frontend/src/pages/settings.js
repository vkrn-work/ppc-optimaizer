import { useState } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

export default function Settings() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [form, setForm] = useState({
    name: '', yandex_login: '', oauth_token: '',
    metrika_counter_id: '', target_cpl: '', target_cpql: '',
  })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  async function handleAdd(e) {
    e.preventDefault()
    setSaving(true); setError(''); setSaved(false)
    try {
      await api.createAccount({
        ...form,
        target_cpl: form.target_cpl ? Number(form.target_cpl) : null,
        target_cpql: form.target_cpql ? Number(form.target_cpql) : null,
      })
      setSaved(true)
      setForm({ name: '', yandex_login: '', oauth_token: '', metrika_counter_id: '', target_cpl: '', target_cpql: '' })
      window.location.reload()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <h1 style={{ fontSize: 20, fontWeight: 500, marginBottom: '1.5rem' }}>Настройки</h1>

      <div style={{ maxWidth: 560 }}>
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <div style={{ fontWeight: 500, marginBottom: '1rem' }}>Подключить новый кабинет</div>
          <form onSubmit={handleAdd} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Field label="Название кабинета *" value={form.name}
              onChange={v => setForm(f => ({ ...f, name: v }))} required />
            <Field label="Логин Яндекс *" value={form.yandex_login}
              onChange={v => setForm(f => ({ ...f, yandex_login: v }))} required />
            <Field label="OAuth-токен *" value={form.oauth_token} type="password"
              onChange={v => setForm(f => ({ ...f, oauth_token: v }))} required />
            <Field label="ID счётчика Метрики" value={form.metrika_counter_id}
              onChange={v => setForm(f => ({ ...f, metrika_counter_id: v }))} />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Field label="Целевой CPL, ₽" value={form.target_cpl} type="number"
                onChange={v => setForm(f => ({ ...f, target_cpl: v }))} />
              <Field label="Целевой CPQL, ₽" value={form.target_cpql} type="number"
                onChange={v => setForm(f => ({ ...f, target_cpql: v }))} />
            </div>
            {error && <div style={{ color: 'var(--red)', fontSize: 12 }}>{error}</div>}
            {saved && <div style={{ color: 'var(--green)', fontSize: 12 }}>Кабинет добавлен успешно</div>}
            <button className="btn btn-primary" type="submit" disabled={saving}>
              {saving ? 'Сохранение...' : 'Подключить кабинет'}
            </button>
          </form>
        </div>

        <div className="card">
          <div style={{ fontWeight: 500, marginBottom: '1rem' }}>Как получить OAuth-токен</div>
          <ol style={{ paddingLeft: 20, fontSize: 13, color: 'var(--text-2)', lineHeight: 2 }}>
            <li>Перейти на <a href="https://oauth.yandex.ru/" target="_blank" style={{ color: 'var(--blue)' }}>oauth.yandex.ru</a></li>
            <li>Создать приложение → платформа «Веб-сервисы»</li>
            <li>Права доступа: <code>direct:api</code> и <code>metrika:read</code></li>
            <li>Callback URI: <code>https://oauth.yandex.ru/verification_code</code></li>
            <li>После создания — нажать «Получить токен»</li>
            <li>Скопировать токен и вставить выше</li>
          </ol>
        </div>
      </div>
    </Layout>
  )
}

function Field({ label, value, onChange, type = 'text', required }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, color: 'var(--text-2)' }}>{label}</span>
      <input type={type} value={value} onChange={e => onChange(e.target.value)}
        required={required} style={{ width: '100%' }} />
    </label>
  )
}
