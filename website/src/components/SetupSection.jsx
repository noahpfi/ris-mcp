import { useEffect, useRef, useState } from 'react'
import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import { Check, Copy } from 'lucide-react'

gsap.registerPlugin(ScrollTrigger)

const PRE = { margin: 0, fontFamily: 'var(--font-mono)', fontSize: '0.8125rem', lineHeight: 1.75, tabSize: 2 }

function CopyBtn({ text }) {
  const [ok, setOk] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setOk(true); setTimeout(() => setOk(false), 1800)
    })
  }
  return (
    <button
      onClick={copy}
      style={{
        position: 'absolute', top: '1rem', right: '1rem',
        display: 'flex', alignItems: 'center', gap: '0.3rem',
        padding: '0.3rem 0.625rem', borderRadius: '0.5rem',
        border: '1px solid var(--border)',
        background: 'var(--surface)', color: ok ? 'var(--amber)' : 'var(--text-muted)',
        fontSize: '0.75rem', fontFamily: 'var(--font-sans)', fontWeight: 500,
        cursor: 'pointer', transition: 'color 0.2s',
      }}
    >
      {ok ? <Check size={12} /> : <Copy size={12} />}
      {ok ? 'Copied' : 'Copy'}
    </button>
  )
}

const INSTALL_TEXT = `git clone https://github.com/noahpfi/ris-mcp
cd ris-mcp
pip install -r requirements.txt`

const CONFIG_TEXT = `{
\t"mcpServers": {
\t\t"ris": {
\t\t\t"command": "python3",
\t\t\t"args": ["-m", "src.server"],
\t\t\t"cwd": "/path/to/ris-mcp"
\t\t}
\t}
}`

export default function SetupSection() {
  const ref = useRef(null)

  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from(ref.current.children, {
        y: 16, opacity: 0, duration: 0.6,
        stagger: 0.1, ease: 'power3.out',
        scrollTrigger: { trigger: ref.current, start: 'top 80%' },
      })
    }, ref)
    return () => ctx.revert()
  }, [])

  return (
    <section
      id="setup"
      ref={ref}
      className="container"
      style={{ paddingBottom: '4rem' }}
    >
      <hr className="divider" style={{ marginBottom: '2.5rem' }} />

      <h2 style={{
        fontWeight: 600, fontSize: '0.8125rem',
        letterSpacing: '0.1em', textTransform: 'uppercase',
        color: 'var(--text-muted)', marginBottom: '2rem',
      }}>
        Setup
      </h2>

      {/* Step 1 */}
      <div style={{ marginBottom: '2rem' }}>
        <p style={{
          fontSize: '0.875rem', fontWeight: 600, color: 'var(--text-2)',
          marginBottom: '0.75rem', letterSpacing: '-0.01em',
        }}>
          1 — Clone and install
        </p>
        <div className="code-block" style={{ position: 'relative' }}>
          <CopyBtn text={INSTALL_TEXT} />
          <pre style={PRE}>
            <span style={{ color: 'var(--amber)' }}>git clone </span>{'https://github.com/noahpfi/ris-mcp\n'}
            <span style={{ color: 'var(--amber)' }}>cd </span>{'ris-mcp\n'}
            <span style={{ color: 'var(--amber)' }}>pip install </span>{'-r requirements.txt'}
          </pre>
        </div>
      </div>

      {/* Step 2 */}
      <div style={{ marginBottom: '2rem' }}>
        <p style={{
          fontSize: '0.875rem', fontWeight: 600, color: 'var(--text-2)',
          marginBottom: '0.75rem', letterSpacing: '-0.01em',
        }}>
          2 — Add to your MCP client
        </p>
        <p style={{
          fontSize: '0.875rem', color: 'var(--text-muted)', marginBottom: '0.875rem', lineHeight: 1.6,
        }}>
          For Claude Desktop, add this to{' '}
          <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>
            claude_desktop_config.json
          </code>{' '}
          and restart. Other MCP clients work the same way.
        </p>
        <div className="code-block" style={{ position: 'relative' }}>
          <CopyBtn text={CONFIG_TEXT} />
          <pre style={PRE}>
            {'{\n'}
            {'\t'}<span className="c-key">"mcpServers":</span>{' {\n'}
            {'\t\t'}<span className="c-key">"ris":</span>{' {\n'}
            {'\t\t\t'}<span className="c-key">"command":</span>{' '}<span className="c-str">"python3"</span>{',\n'}
            {'\t\t\t'}<span className="c-key">"args":</span>{' '}<span className="c-str">["-m", "src.server"]</span>{',\n'}
            {'\t\t\t'}<span className="c-key">"cwd":</span>{' '}<span className="c-str">"/path/to/ris-mcp"</span>{'\n'}
            {'\t\t}\n'}
            {'\t}\n'}
            {'}'}
          </pre>
        </div>
      </div>

      {/* Step 3 */}
      <div>
        <p style={{
          fontSize: '0.875rem', fontWeight: 600, color: 'var(--text-2)',
          marginBottom: '0.75rem', letterSpacing: '-0.01em',
        }}>
          3 — Ask something
        </p>
        <div
          style={{
            background: 'var(--bg-alt)',
            border: '1px solid var(--border)',
            borderRadius: '1.25rem',
            padding: '1.25rem 1.5rem',
          }}
        >
          {[
            '"Wo steht das mit GuV im UGB?"',
            '"Was sagt § 879 ABGB?"',
            '"Wann wurde das GmbHG zuletzt geändert?"',
          ].map((q, i) => (
            <div
              key={i}
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '0.8125rem',
                color: 'var(--text-muted)',
                padding: '0.25rem 0',
                borderBottom: i < 2 ? '1px solid var(--border)' : 'none',
              }}
            >
              {q}
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
