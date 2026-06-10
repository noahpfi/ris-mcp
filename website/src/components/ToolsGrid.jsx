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
  { name: 'who_mentions', desc: 'find laws that cite a given §  (local index)' },
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
            <span className="tool-name">{t.name}</span>
            <span className="tool-desc">{t.desc}</span>
          </div>
        ))}
      </div>

      <p style={{
        marginTop: '1.5rem', fontSize: '0.875rem',
        color: 'var(--text-muted)', lineHeight: 1.6,
      }}>
        <span style={{ color: 'var(--amber)' }}>who_mentions</span> requires a local full-text
        index — clone the project and build it by running{' '}
        <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8125rem', whiteSpace: 'nowrap' }}>
          python -m src.index
        </code>{' '}
      </p>
    </section>
  )
}
