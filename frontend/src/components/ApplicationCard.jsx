import { Link } from 'react-router-dom'

export function ApplicationCard({ app }) {
  const initials = String(app.displayName || app.applicationSlug || 'DW')
    .split(/[\s_-]+/)
    .slice(0, 2)
    .map(word => word[0])
    .join('')
    .toUpperCase()

  return (
    <Link to={`/applications/${app.applicationSlug}`} className="app-card">
      <div className="app-card__header">
        <div className="app-card__icon">{initials}</div>
        <div className="app-card__identity">
          <div className="app-card__name">{app.displayName}</div>
          <div className="app-card__url">{app.repositoryUrl}</div>
        </div>
        <span className="app-card__arrow" aria-hidden="true">↗</span>
      </div>
      <div className="app-card__divider" />
      <div className="app-card__footer">
        <div className="app-card__langs">
          {(app.languages || []).slice(0, 3).map(language => (
            <span key={language} className="lang-badge">
              <span className="lang-badge__dot" />
              {language}
            </span>
          ))}
        </div>
        <span className={`status-badge status-badge--${app.status}`}>
          {app.status}
        </span>
      </div>
    </Link>
  )
}
