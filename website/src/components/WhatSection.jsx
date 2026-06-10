import { useEffect, useRef } from 'react'
import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(ScrollTrigger)

export default function WhatSection() {
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
      ref={ref}
      className="container"
      style={{ paddingTop: '3rem', paddingBottom: '3.5rem' }}
    >
      <hr className="divider" style={{ marginBottom: '3rem' }} />

      <p style={{
        fontSize: '1rem', color: 'var(--text-2)',
        lineHeight: 1.8, maxWidth: '580px', margin: '0 0 1.5rem',
      }}>
        <a
          href="https://www.ris.bka.gv.at"
          target="_blank"
          rel="noopener noreferrer"
          className="subtle"
        >
          RIS
        </a>{' '}
        is the Austrian federal government's legal information system — ~250,000 documents
        covering every statute, regulation, and gazette entry. It's public and always current,
        but not exactly AI-friendly to query directly.
      </p>

      <p style={{
        fontSize: '1rem', color: 'var(--text-2)',
        lineHeight: 1.8, maxWidth: '580px', margin: 0,
      }}>
        This MCP server wraps the{' '}
        <a
          href="https://data.bka.gv.at/ris/api/v2.6/"
          target="_blank"
          rel="noopener noreferrer"
          className="subtle"
        >
          RIS OGD API v2.6
        </a>{' '}
        and exposes it as tools Claude can call. Ask a question about Austrian law and it looks
        up the actual text — no hallucinating paragraph numbers, no training cutoff issues.
      </p>
    </section>
  )
}
