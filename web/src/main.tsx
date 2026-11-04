import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ServerGate } from './components/ServerGate'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ServerGate>
      <App />
    </ServerGate>
  </StrictMode>,
)
