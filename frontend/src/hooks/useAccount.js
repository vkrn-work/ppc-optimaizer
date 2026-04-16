import { useState, useEffect, createContext, useContext } from 'react'
import { api } from '../utils/api'

const AccountContext = createContext(null)

export function AccountProvider({ children }) {
  const [accounts, setAccounts] = useState([])
  const [accountId, setAccountId] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getAccounts().then(data => {
      setAccounts(data)
      if (data.length > 0) {
        const saved = typeof window !== 'undefined' ? localStorage.getItem('account_id') : null
        const id = saved ? Number(saved) : data[0].id
        setAccountId(id)
      }
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  function switchAccount(id) {
    setAccountId(id)
    if (typeof window !== 'undefined') localStorage.setItem('account_id', id)
  }

  const account = accounts.find(a => a.id === accountId)

  return (
    <AccountContext.Provider value={{ account, accounts, accountId, switchAccount, loading }}>
      {children}
    </AccountContext.Provider>
  )
}

export function useAccount() {
  return useContext(AccountContext)
}
