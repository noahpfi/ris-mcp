const linkStyle = { whiteSpace: 'nowrap' }

export default function Footer() {
  return (
    <footer
      style={{
        borderTop: '1px solid var(--border)',
        padding: '1.5rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '1rem',
        fontSize: '0.75rem',
        color: 'var(--text-muted)',
        lineHeight: 1.65,
      }}
    >
      <p style={{ margin: 0, maxWidth: '100%' }}>
        Independent project &mdash; not affiliated with, endorsed by, or operated by the
        Republic of Austria, the Bundeskanzleramt, or RIS. No warranty of accuracy, currency,
        completeness, or availability: consolidated law in RIS is not legally binding, only the
        wording in the Bundesgesetzblatt (&ldquo;BGBl authentisch&rdquo;) is, and nothing this
        server returns is legal advice. Provided free and as-is; liability excluded as far as
        the law permits, intent and gross negligence unaffected. No cookies, no analytics, no
        third-party requests; the MCP endpoint keeps no access log &mdash; caller IPs stay in
        memory for at most two minutes to enforce the rate limit.
      </p>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '0.5rem 1rem',
        }}
      >
        <span style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap', fontFamily: 'var(--font-mono)' }}>
          <span>ris-mcp · MIT</span>
          <a
            href="https://noahpfister.com/impressum"
            target="_blank"
            rel="noopener noreferrer"
            className="subtle"
            style={linkStyle}
          >
            Impressum
          </a>
          <a
            href="https://noahpfister.com/datenschutz.html"
            target="_blank"
            rel="noopener noreferrer"
            className="subtle"
            style={linkStyle}
          >
            Datenschutz
          </a>
        </span>
        <span>
          Data: RIS, Bundeskanzleramt &Ouml;sterreich &middot; RIS OGD API v2.6 &middot;{' '}
          <a
            href="https://creativecommons.org/licenses/by/4.0/deed.en"
            target="_blank"
            rel="noopener noreferrer"
            className="subtle"
            style={linkStyle}
          >
            CC BY 4.0
          </a>
        </span>
      </div>
    </footer>
  )
}
