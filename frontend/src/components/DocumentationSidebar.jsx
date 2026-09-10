export function DocumentationSidebar({ documents, activeSlug, onSelect }) {
  return (
    <div className="doc-sidebar">
      <div className="doc-sidebar__header">
        <span className="doc-sidebar__eyebrow">Workspace</span>
        <div className="doc-sidebar__title">Documentation</div>
      </div>
      {documents.map(doc => (
        <div
          key={doc.slug}
          className={`doc-sidebar__item${doc.slug === activeSlug ? ' doc-sidebar__item--active' : ''}`}
          onClick={() => onSelect(doc.slug)}
        >
          <span className="doc-sidebar__icon">
            {doc.kind === 'overview' ? '◫' : '◇'}
          </span>
          <span className="doc-sidebar__label">{doc.title}</span>
          <span className="doc-sidebar__chevron">›</span>
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
