import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Self-hosted on purpose: a Google Fonts <link> would send every visitor's IP
// to Google LLC, which the privacy notice would then have to declare.
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
