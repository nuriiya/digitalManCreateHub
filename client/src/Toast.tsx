import { createContext, useCallback, useContext, useState } from 'react'

interface ToastApi { toast: (msg: string, kind?: 'ok' | 'err') => void }
const ToastCtx = createContext<ToastApi>({ toast: () => {} })

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [msg, setMsg] = useState<{ text: string; kind: string } | null>(null)
  const toast = useCallback((text: string, kind: 'ok' | 'err' = 'ok') => {
    setMsg({ text, kind })
    setTimeout(() => setMsg(null), 2600)
  }, [])
  return (
    <ToastCtx.Provider value={{ toast }}>
      {children}
      {msg && <div className={`toast ${msg.kind}`}>{msg.text}</div>}
    </ToastCtx.Provider>
  )
}

export const useToast = () => useContext(ToastCtx)
