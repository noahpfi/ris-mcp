export default function Footer() {
  return (
    <footer
      style={{
        borderTop: '1px solid var(--border)',
        padding: '1.25rem 1.5rem',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: '0.5rem',
      }}
    >
      <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
        ris-mcp · MIT
      </span>
      <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
        RIS OGD API v2.6
      </span>
    </footer>
  )
}
