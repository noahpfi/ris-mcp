import { useEffect, useRef } from 'react'
import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(ScrollTrigger)

function GithubIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
    </svg>
  )
}

export default function OutroSection() {
  const ref = useRef(null)

  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from(ref.current.children, {
        y: 16, opacity: 0, duration: 0.6,
        stagger: 0.1, ease: 'power3.out',
        scrollTrigger: { trigger: ref.current, start: 'top 85%' },
      })
    }, ref)
    return () => ctx.revert()
  }, [])

  return (
    <section
      ref={ref}
      className="container"
      style={{ paddingBottom: '5rem' }}
    >
      <hr className="divider" style={{ marginBottom: '2.5rem' }} />

      <p style={{
        fontSize: '1rem', color: 'var(--text-muted)',
        lineHeight: 1.8, maxWidth: '520px', marginBottom: '1.5rem',
      }}>
        Built by{' '}
        <a
          href="https://noahpfister.com"
          target="_blank"
          rel="noopener noreferrer"
          className="subtle"
        >
          Noah
        </a>{' '}
        while studying Austrian commercial law and being tired of copy-pasting
        from RIS into ChatGPT. MIT licensed. PRs welcome.
      </p>

      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
        <a
          href="https://github.com/noahpfi/ris-mcp"
          target="_blank"
          rel="noopener noreferrer"
        >
          <button className="btn btn-outline" style={{ gap: '0.4rem' }}>
            <GithubIcon size={14} />
            github.com/noahpfi/ris-mcp
          </button>
        </a>
      </div>

      <p style={{
        marginTop: '2.5rem',
        fontSize: '0.8125rem', color: 'var(--text-muted)',
        lineHeight: 1.6, maxWidth: '460px',
      }}>
        Not affiliated with the Austrian government or RIS.
        Always verify legal information with a qualified professional.
      </p>
    </section>
  )
}
