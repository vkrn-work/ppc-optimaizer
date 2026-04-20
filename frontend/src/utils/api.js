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
  triggerHistoricalSync: (id, days = 90) => req(`/accounts/${id}/sync?days=${days}`, { method: 'POST' }),

  // period: yesterday | 3d | week | month
  getDashboard: (id, period = 'week') => req(`/accounts/${id}/dashboard?period=${period}`),
  getCampaigns: (id, period = 'week', activeOnly = false) =>
    req(`/accounts/${id}/campaigns?period=${period}&active_only=${activeOnly}`),
  getKeywords: (id, params = '') => req(`/accounts/${id}/keywords${params}`),

  getSuggestions: (id, params = '') => req(`/accounts/${id}/suggestions${params}`),
  actionSuggestion: (id, data) =>
    req(`/suggestions/${id}/action`, { method: 'POST', body: JSON.stringify(data) }),

  getAnalyses: (id) => req(`/accounts/${id}/analyses`),
  getHypotheses: (id) => req(`/accounts/${id}/hypotheses`),
  createHypothesis: (id, data) =>
    req(`/accounts/${id}/hypotheses`, { method: 'POST', body: JSON.stringify(data) }),
  getRules: (id) => req(`/accounts/${id}/rules`),

  getMetrikaSnapshot: (id) => req(`/accounts/${id}/metrika-snapshot`),
  getSearchQueries: (id, params = '') => req(`/accounts/${id}/search-queries${params}`),
  getDiagnostics: (id) => req(`/accounts/${id}/diagnostics`),
}
