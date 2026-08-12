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

const HOSTED_URL = 'https://ris-mcp.noahpfister.com/mcp'
const HOSTED_TEXT = `claude mcp add --transport http ris ${HOSTED_URL}`

const HOSTED_CONFIG_TEXT = `{
\t"mcpServers": {
\t\t"ris": {
\t\t\t"type": "http",
\t\t\t"url": "${HOSTED_URL}"
\t\t}
\t}
}`

const INSTALL_TEXT = `git clone https://github.com/noahpfi/ris-mcp
cd ris-mcp
pip install -r requirements.txt
python -m src.index          # builds the cross-reference index`

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

      {/* Hosted */}
      <div style={{ marginBottom: '2rem' }}>
        <p style={{
          fontSize: '0.875rem', fontWeight: 600, color: 'var(--text-2)',
          marginBottom: '0.75rem', letterSpacing: '-0.01em',
        }}>
          Hosted — nothing to install
        </p>
        <p style={{
          fontSize: '0.875rem', color: 'var(--text-muted)', marginBottom: '0.875rem', lineHeight: 1.6,
        }}>
          Point your client at the URL. No Python, no clone, no API key. Gives you the seven
          tools that query RIS live.
        </p>
        <div className="code-block" style={{ position: 'relative' }}>
          <CopyBtn text={HOSTED_TEXT} />
          <pre style={PRE}>
            <span style={{ color: 'var(--amber)' }}>claude mcp add </span>
            {`--transport http ris ${HOSTED_URL}`}
          </pre>
        </div>
        <p style={{
          fontSize: '0.875rem', color: 'var(--text-muted)',
          margin: '0.875rem 0', lineHeight: 1.6,
        }}>
          For Claude Desktop and other clients, add it as a remote server in{' '}
          <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>
            claude_desktop_config.json
          </code>{' '}
          and restart.
        </p>
        <div className="code-block" style={{ position: 'relative' }}>
          <CopyBtn text={HOSTED_CONFIG_TEXT} />
          <pre style={PRE}>
            {'{\n'}
            {'\t'}<span className="c-key">"mcpServers":</span>{' {\n'}
            {'\t\t'}<span className="c-key">"ris":</span>{' {\n'}
            {'\t\t\t'}<span className="c-key">"type":</span>{' '}<span className="c-str">"http"</span>{',\n'}
            {'\t\t\t'}<span className="c-key">"url":</span>{' '}<span className="c-str">"{HOSTED_URL}"</span>{'\n'}
            {'\t\t}\n'}
            {'\t}\n'}
            {'}'}
          </pre>
        </div>
      </div>

      {/* Self-host */}
      <div style={{ marginBottom: '2rem' }}>
        <p style={{
          fontSize: '0.875rem', fontWeight: 600, color: 'var(--text-2)',
          marginBottom: '0.75rem', letterSpacing: '-0.01em',
        }}>
          Self-host — adds cross-references
        </p>
        <p style={{
          fontSize: '0.875rem', color: 'var(--text-muted)', marginBottom: '0.875rem', lineHeight: 1.6,
        }}>
          Run it locally to get{' '}
          <span style={{ color: 'var(--amber)', fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>
            who_mentions
          </span>{' '}
          on top. Building the index crawls all 440,000 documents at about 9 per second, so
          leave it running overnight; it lands at roughly 600&nbsp;MB on disk. Queries against
          it are local and instant afterwards, and it resumes if interrupted.
        </p>
        <div className="code-block" style={{ position: 'relative' }}>
          <CopyBtn text={INSTALL_TEXT} />
          <pre style={PRE}>
            <span style={{ color: 'var(--amber)' }}>git clone </span>{'https://github.com/noahpfi/ris-mcp\n'}
            <span style={{ color: 'var(--amber)' }}>cd </span>{'ris-mcp\n'}
            <span style={{ color: 'var(--amber)' }}>pip install </span>{'-r requirements.txt\n'}
            <span style={{ color: 'var(--amber)' }}>python </span>{'-m src.index'}
            <span style={{ color: 'var(--text-muted)' }}>{'          # builds the index'}</span>
          </pre>
        </div>
        <p style={{
          fontSize: '0.875rem', color: 'var(--text-muted)',
          margin: '0.875rem 0', lineHeight: 1.6,
        }}>
          Then point your client at the local process instead:
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

      {/* Ask something */}
      <div>
        <p style={{
          fontSize: '0.875rem', fontWeight: 600, color: 'var(--text-2)',
          marginBottom: '0.75rem', letterSpacing: '-0.01em',
        }}>
          Ask something
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
