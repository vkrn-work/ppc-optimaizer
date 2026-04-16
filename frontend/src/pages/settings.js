import { useState, useEffect } from 'react'
import Layout from '../components/Layout'
import { useAccount } from '../hooks/useAccount'
import { api } from '../utils/api'

export default function Settings() {
  const { account, accounts, accountId, switchAccount } = useAccount()
  const [allAccounts, setAllAccounts] = useState([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState(null)
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ name: '', yandex_login: '', oauth_token: '', metrika_counter_id: '', target_cpl: '', target_cpql: '' })
  const [editForm, setEditForm] = useState({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(null)

  useEffect(() => {
    loadAccounts()
  }, [])

  async function loadAccounts() {
    setLoading(true)
    try {
      const data = await api.getAccounts()
      setAllAccounts(data)
    } catch(e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleAdd(e) {
    e.preventDefault()
    setSaving(true); setError(''); setSuccess('')
    try {
      await api.createAccount({
        ...form,
        target_cpl: form.target_cpl ? Number(form.target_cpl) : null,
        target_cpql: form.target_cpql ? Number(form.target_cpql) : null,
      })
      setSuccess('Кабинет добавлен')
      setForm({ name: '', yandex_login: '', oauth_token: '', metrika_counter_id: '', target_cpl: '', target_cpql: '' })
      setShowAdd(false)
      loadAccounts()
    } catch(e) { setError(e.message) }
    finally { setSaving(false) }
  }

  async function handleEdit(e) {
    e.preventDefault()
    setSaving(true); setError(''); setSuccess('')
    try {
      await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/v1/accounts/${editingId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          oauth_token: editForm.oauth_token || undefined,
          metrika_counter_id: editForm.metrika_counter_id || undefined,
          target_cpl: editForm.target_cpl ? Number(editForm.target_cpl) : undefined,
          target_cpql: editForm.target_cpql ? Number(editForm.target_cpql) : undefined,
        }),
      })
      setSuccess('Настройки обновлены')
      setEditingId(null)
      loadAccounts()
    } catch(e) { setError(e.message) }
    finally { setSaving(false) }
  }

  async function handleDelete(id) {
    setSaving(true); setError('')
    try {
      await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/v1/accounts/${id}`, {
        method: 'DELETE',
      })
      setSuccess('Кабинет удалён')
      setConfirmDelete(null)
      loadAccounts()
    } catch(e) { setError(e.message) }
    finally { setSaving(false) }
  }

  return (
    <Layout account={account} accounts={accounts} onAccountChange={switchAccount}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <h1 style={{ fontSize: 20, fontWeight: 500 }}>Настройки кабинетов</h1>
        <button className="btn btn-primary" onClick={() => { setShowAdd(v => !v); setError('') }}>
          {showAdd ? 'Отмена' : '+ Добавить кабинет'}
        </button>
      </div>

      {error && <div style={{ color: 'var(--red)', fontSize: 13, marginBottom: 12, padding: '8px 12px', background: 'var(--red-bg)', borderRadius: 'var(--radius)' }}>{error}</div>}
      {success && <div style={{ color: 'var(--green)', fontSize: 13, marginBottom: 12, padding: '8px 12px', background: 'var(--green-bg)', borderRadius: 'var(--radius)' }}>{success}</div>}

      {/* Форма добавления */}
      {showAdd && (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <div style={{ fontWeight: 500, marginBottom: '1rem' }}>Новый кабинет</div>
          <form onSubmit={handleAdd} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Field label="Название *" value={form.name} onChange={v => setForm(f => ({...f, name: v}))} required />
            <Field label="Логин Яндекс *" value={form.yandex_login} onChange={v => setForm(f => ({...f, yandex_login: v}))} required />
            <Field label="OAuth-токен *" type="password" value={form.oauth_token} onChange={v => setForm(f => ({...f, oauth_token: v}))} required />
            <Field label="ID счётчика Метрики" value={form.metrika_counter_id} onChange={v => setForm(f => ({...f, metrika_counter_id: v}))} />
            <Field label="Целевой CPL, ₽" type="number" value={form.target_cpl} onChange={v => setForm(f => ({...f, target_cpl: v}))} />
            <Field label="Целевой CPQL, ₽" type="number" value={form.target_cpql} onChange={v => setForm(f => ({...f, target_cpql: v}))} />
            <div style={{ gridColumn: '1/-1', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button type="button" className="btn" onClick={() => setShowAdd(false)}>Отмена</button>
              <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Сохранение...' : 'Добавить'}</button>
            </div>
          </form>
        </div>
      )}

      {/* Список кабинетов */}
      {loading ? (
        <div style={{ color: 'var(--text-3)', padding: '2rem' }}>Загрузка...</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {allAccounts.map(acc => (
            <div key={acc.id} className="card">
              {editingId === acc.id ? (
                /* Форма редактирования */
                <form onSubmit={handleEdit}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                    <div style={{ fontWeight: 500 }}>{acc.name}</div>
                    <button type="button" className="btn" onClick={() => setEditingId(null)} style={{ fontSize: 12 }}>Отмена</button>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <Field label="Новый OAuth-токен" type="password" value={editForm.oauth_token || ''} onChange={v => setEditForm(f => ({...f, oauth_token: v}))} />
                    <Field label="ID счётчика Метрики" value={editForm.metrika_counter_id || ''} onChange={v => setEditForm(f => ({...f, metrika_counter_id: v}))} />
                    <Field label="Целевой CPL, ₽" type="number" value={editForm.target_cpl || ''} onChange={v => setEditForm(f => ({...f, target_cpl: v}))} />
                    <Field label="Целевой CPQL, ₽" type="number" value={editForm.target_cpql || ''} onChange={v => setEditForm(f => ({...f, target_cpql: v}))} />
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
                    <button type="submit" className="btn btn-success" disabled={saving}>{saving ? 'Сохранение...' : 'Сохранить'}</button>
                  </div>
                </form>
              ) : (
                /* Просмотр */
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                        <span style={{ fontWeight: 500, fontSize: 15 }}>{acc.name}</span>
                        {acc.id === accountId && <span className="badge badge-ok">Активный</span>}
                        <span className={`badge ${acc.is_active ? 'badge-ok' : 'badge-bad'}`}>
                          {acc.is_active ? 'Включён' : 'Отключён'}
                        </span>
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--text-2)', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 24px' }}>
                        <span>Логин: <strong>{acc.yandex_login}</strong></span>
                        <span>Метрика: <strong>{acc.metrika_counter_id || '—'}</strong></span>
                        <span>CPL: <strong>{acc.target_cpl ? acc.target_cpl + ' ₽' : '—'}</strong></span>
                        <span>CPQL: <strong>{acc.target_cpql ? acc.target_cpql + ' ₽' : '—'}</strong></span>
                        <span>Создан: <strong>{new Date(acc.created_at).toLocaleDateString('ru-RU')}</strong></span>
                        <span>Синхронизация: <strong>{acc.last_sync_at ? new Date(acc.last_sync_at).toLocaleString('ru-RU') : 'Не выполнялась'}</strong></span>
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                      <button className="btn" style={{ fontSize: 12 }} onClick={() => {
                        setEditingId(acc.id)
                        setEditForm({
                          metrika_counter_id: acc.metrika_counter_id || '',
                          target_cpl: acc.target_cpl || '',
                          target_cpql: acc.target_cpql || '',
                        })
                        setError(''); setSuccess('')
                      }}>Редактировать</button>
                      <button className="btn" style={{ fontSize: 12 }} onClick={async () => {
                        try {
                          await api.triggerSync(acc.id)
                          setSuccess(`Сбор данных запущен для "${acc.name}"`)
                        } catch(e) { setError(e.message) }
                      }}>↻ Собрать</button>
                      <button className="btn btn-danger" style={{ fontSize: 12 }} onClick={() => setConfirmDelete(acc)}>Удалить</button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Инструкция по токену */}
      <div className="card" style={{ marginTop: '1.5rem' }}>
        <div style={{ fontWeight: 500, marginBottom: '0.75rem' }}>Как получить OAuth-токен</div>
        <ol style={{ paddingLeft: 20, fontSize: 13, color: 'var(--text-2)', lineHeight: 2 }}>
          <li>Зайти на <a href="https://oauth.yandex.ru/" target="_blank" style={{ color: 'var(--blue)' }}>oauth.yandex.ru</a> → ваше приложение</li>
          <li>Нажать <strong>Получить токен</strong> → подтвердить доступ</li>
          <li>Скопировать токен и вставить в поле выше</li>
        </ol>
      </div>

      {/* Модал подтверждения удаления */}
      {confirmDelete && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.3)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }} onClick={e => e.target === e.currentTarget && setConfirmDelete(null)}>
          <div style={{ background: 'var(--surface)', borderRadius: 'var(--radius-lg)', padding: '1.5rem', width: 400 }}>
            <div style={{ fontWeight: 500, marginBottom: 8 }}>Удалить кабинет?</div>
            <p style={{ fontSize: 13, color: 'var(--text-2)', marginBottom: 16 }}>
              «{confirmDelete.name}» ({confirmDelete.yandex_login}) будет удалён вместе со всеми данными. Это действие необратимо.
            </p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setConfirmDelete(null)}>Отмена</button>
              <button className="btn btn-danger" disabled={saving} onClick={() => handleDelete(confirmDelete.id)}>
                {saving ? 'Удаление...' : 'Удалить'}
              </button>
            </div>
          </div>
        </div>
      )}
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
