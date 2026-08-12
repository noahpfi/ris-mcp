import { useEffect, useRef } from 'react'
import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(ScrollTrigger)

const TOOLS = [
  { name: 'search_law', desc: 'full-text keyword search across Bundesrecht' },
  { name: 'get_paragraph', desc: 'fetch § or a range, e.g. §§ 200–210 UGB' },
  { name: 'get_paragraph_at', desc: 'historical version of a § on a given date' },
  { name: 'get_statute', desc: 'preamble + first paragraphs of a statute' },
  { name: 'get_law_outline', desc: 'full table of contents with § headings' },
  { name: 'lookup_bgbl', desc: 'Bundesgesetzblatt entry by number' },
  { name: 'get_amendment_timeline', desc: 'ordered list of every BGBl that amended a law' },
  { name: 'who_mentions', desc: 'find laws that cite a given § — self-host only', selfHost: true },
]

export default function ToolsGrid() {
  const ref = useRef(null)

  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from('.tool-row', {
        y: 12, opacity: 0, duration: 0.5,
        stagger: 0.06, ease: 'power3.out',
        scrollTrigger: { trigger: ref.current, start: 'top 80%' },
      })
    }, ref)
    return () => ctx.revert()
  }, [])

  return (
    <section
      ref={ref}
      className="container"
      style={{ paddingBottom: '3.5rem' }}
    >
      <hr className="divider" style={{ marginBottom: '2.5rem' }} />

      <h2 style={{
        fontWeight: 600, fontSize: '0.8125rem',
        letterSpacing: '0.1em', textTransform: 'uppercase',
        color: 'var(--text-muted)', marginBottom: '1.5rem',
      }}>
        Tools
      </h2>

      <div>
        {TOOLS.map(t => (
          <div key={t.name} className="tool-row">
            <span className="tool-name">
              {t.name}
              {t.selfHost && (
                <span style={{
                  marginLeft: '0.5rem', padding: '0.05rem 0.4rem',
                  borderRadius: '0.375rem', border: '1px solid var(--border)',
                  fontFamily: 'var(--font-sans)', fontSize: '0.6875rem',
                  fontWeight: 500, color: 'var(--text-muted)', whiteSpace: 'nowrap',
                }}>
                  self-host
                </span>
              )}
            </span>
            <span className="tool-desc">{t.desc}</span>
          </div>
        ))}
      </div>

      <p style={{
        marginTop: '1.5rem', fontSize: '0.875rem',
        color: 'var(--text-muted)', lineHeight: 1.6,
      }}>
        The first seven query RIS live and are available on the hosted server.{' '}
        <span style={{ color: 'var(--amber)' }}>who_mentions</span> searches a full-text index
        of every Austrian federal provision to find the ones citing a given §. That index is
        roughly 600&nbsp;MB and has to be crawled, so it is not part of the hosted server —{' '}
        <a href="#setup" style={{ color: 'var(--amber)' }}>run it yourself</a> to get it.
      </p>
    </section>
  )
}
