export function DocumentationSidebar({ documents, activeSlug, onSelect, headings = [] }) {
  return (
    <div className="doc-sidebar">
      <div className="doc-sidebar__header">
        <span className="doc-sidebar__eyebrow">Workspace</span>
        <div className="doc-sidebar__title">Documentation</div>
      </div>
      {documents.map(doc => (
        <div key={doc.slug}>
        <button
          type="button"
          className={`doc-sidebar__item${doc.slug === activeSlug ? ' doc-sidebar__item--active' : ''}`}
          title={doc.title}
          onClick={() => onSelect(doc.slug)}
        >
          <span className="doc-sidebar__icon">
            {doc.kind === 'overview' ? '◫' : '◇'}
          </span>
          <span className="doc-sidebar__label">{doc.title}</span>
          <span className="doc-sidebar__chevron">›</span>
        </button>
        {doc.slug === activeSlug && headings.length > 0 && (
          <nav className="doc-sidebar__headings" aria-label={`${doc.title} sections`}>
            {headings.map(heading => (
              <button key={heading.id} type="button"
                style={{ paddingLeft: 12 + (heading.depth - 1) * 10 }}
                onClick={() => document.getElementById(heading.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
                {heading.title}
              </button>
            ))}
          </nav>
        )}
        </div>
      ))}
      {documents.length === 0 && (
        <div className="doc-sidebar__empty">
          No documentation yet.
        </div>
      )}
    </div>
  )
}
