import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// self-hosted on purpose; Google Fonts link leaks visitor IPs to Google LLC, privacy notice must declare
import '@fontsource-variable/space-grotesk'
import '@fontsource-variable/jetbrains-mono'
import './index.css'
import App from './App.jsx'

window.addEventListener('message', (e) => {
  if (e.data?.type === 'portfolio-theme') {
    document.documentElement.setAttribute('data-theme', e.data.theme)
  }
})

if (window !== window.top) document.documentElement.classList.add('in-iframe')

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
