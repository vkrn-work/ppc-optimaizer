import { useState, useEffect } from 'react'
import { api } from '../utils/api'

export function useAccount() {
  const [accounts, setAccounts] = useState([])
  const [accountId, setAccountId] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getAccounts()
      .then(data => {
        setAccounts(data)
        if (data.length > 0) {
          const saved = typeof window !== 'undefined' ? localStorage.getItem('accountId') : null
          const id = saved ? Number(saved) : data[0].id
          const exists = data.find(a => a.id === id)
          setAccountId(exists ? id : data[0].id)
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const account = accounts.find(a => a.id === accountId) || null

  function switchAccount(id) {
    setAccountId(id)
    if (typeof window !== 'undefined') localStorage.setItem('accountId', id)
  }

  return { account, accounts, accountId, switchAccount, loading }
}
