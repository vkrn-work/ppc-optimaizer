const BASE = 'https://ppc-optimaizer-production.up.railway.app'

async function req(path, options = {}) {
  const res = await fetch(`${BASE}/api/v1${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  getAccounts: () => req('/accounts'),
  createAccount: (data) => req('/accounts', { method: 'POST', body: JSON.stringify(data) }),
  updateAccount: (id, data) => req(`/accounts/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteAccount: (id) => req(`/accounts/${id}`, { method: 'DELETE' }),
  triggerSync: (id) => req(`/accounts/${id}/sync`, { method: 'POST' }),

  getDashboard: (id) => req(`/accounts/${id}/dashboard`),
  getCampaigns: (id) => req(`/accounts/${id}/campaigns`),
  getKeywords: (id, params = '') => req(`/accounts/${id}/keywords${params}`),

  getSuggestions: (id, params = '') => req(`/accounts/${id}/suggestions${params}`),
  actionSuggestion: (id, data) =>
    req(`/suggestions/${id}/action`, { method: 'POST', body: JSON.stringify(data) }),

  getAnalyses: (id) => req(`/accounts/${id}/analyses`),
  getHypotheses: (id) => req(`/accounts/${id}/hypotheses`),
  getRules: (id) => req(`/accounts/${id}/rules`),
}
