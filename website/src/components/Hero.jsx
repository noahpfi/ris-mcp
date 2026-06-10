import { useEffect, useRef } from 'react'
import { gsap } from 'gsap'
import { Scale } from 'lucide-react'

export default function Hero() {
  const ref = useRef(null)

  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from(ref.current.children, {
        y: 20, opacity: 0, duration: 0.7,
        stagger: 0.1, ease: 'power3.out', delay: 0.25,
      })
    }, ref)
    return () => ctx.revert()
  }, [])

  return (
    <section
      ref={ref}
      className="container"
      style={{ paddingTop: '7rem', paddingBottom: '4rem' }}
    >
      <div
        className="float"
        style={{ marginBottom: '1.5rem', display: 'inline-block', color: 'var(--amber)' }}
      >
        <Scale size={44} strokeWidth={1.5} />
      </div>

      <h1 style={{
        fontWeight: 700,
        fontSize: 'clamp(2rem, 6vw, 3.5rem)',
        letterSpacing: '-0.03em',
        lineHeight: 1.1,
        color: 'var(--text)',
        marginBottom: '1.25rem',
      }}>
        ris-mcp
      </h1>

      <p style={{
        fontSize: 'clamp(1rem, 2.5vw, 1.175rem)',
        color: 'var(--text-muted)',
        lineHeight: 1.7,
        maxWidth: '520px',
        marginBottom: '2rem',
      }}>
        An MCP server that connects Claude (or any MCP client) to{' '}
        <a
          href="https://www.ris.bka.gv.at"
          target="_blank"
          rel="noopener noreferrer"
          className="subtle"
        >
          RIS
        </a>
        {' '}— Austria's official legal database. Ask about statutes,
        paragraphs, and amendments directly in your chat.
      </p>

      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
        <a href="#setup">
          <button className="btn btn-fill">
            <span className="slide" />
            <span>How to add</span>
          </button>
        </a>
        <a
          href="https://github.com/noahpfi/ris-mcp"
          target="_blank"
          rel="noopener noreferrer"
        >
          <button className="btn btn-outline">
            View source
          </button>
        </a>
      </div>
    </section>
  )
}
