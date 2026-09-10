import {
  useCallback,
  useEffect,
  useState,
} from 'react'

import { ChatPanel } from '../components/ChatPanel.jsx'
import { ApplicationCard } from '../components/ApplicationCard.jsx'

import {
  getOrCreateSession,
  listApplications,
} from '../api/docwikiApi.js'

export function HomePage() {
  const [session, setSession] = useState({
    userId: null,
    sessionId: null,
  })

  const [apps, setApps] = useState([])
  const [loadingApps, setLoadingApps] = useState(true)

  // Create or restore the dedicated homepage session.
  useEffect(() => {
    let cancelled = false

    getOrCreateSession({
      pageMode: 'home',
      applicationSlug: null,
    })
      .then(currentSession => {
        if (!cancelled) {
          setSession(currentSession)
        }
      })
      .catch(error => {
        console.error(
          'Homepage session creation failed:',
          error
        )
      })

    return () => {
      cancelled = true
    }
  }, [])

  const fetchApps = useCallback(() => {
    setLoadingApps(true)

    listApplications()
      .then(setApps)
      .catch(error => {
        console.error(
          'Failed to load applications:',
          error
        )
      })
      .finally(() => {
        setLoadingApps(false)
      })
  }, [])

  useEffect(() => {
    fetchApps()
  }, [fetchApps])

  const handleToolResult = (
    name,
    result
  ) => {
    if (
      (
        name === 'onboard_repository' &&
        result?.ok
      ) ||
      name === 'finalize_documentation'
    ) {
      setTimeout(fetchApps, 800)
    }
  }

  return (
    <div className="page">
      <header className="header">
        <div className="header__brand">
          <div className="header__logo">D</div>
          <div>
            <div className="header__title">DocWiki</div>
            <div className="header__tagline">Codebase intelligence</div>
          </div>
        </div>

        <div className="header__status">
          <span className="header__status-dot" />
          AI workspace online
        </div>
      </header>

      <main className="home-body">
        <section className="home-hero">
          <div className="home-hero__eyebrow">
            <span>✦</span>
            Documentation that understands your code
          </div>

          <h1>
            Turn complex codebases into
            <span> clear, living knowledge.</span>
          </h1>

          <p>
            Connect a public GitHub repository and let DocWiki map the
            architecture, generate documentation, and answer questions
            with code-aware context.
          </p>

          <div className="home-hero__proof">
            <span><b>01</b> Connect repository</span>
            <span className="home-hero__proof-line" />
            <span><b>02</b> Analyze codebase</span>
            <span className="home-hero__proof-line" />
            <span><b>03</b> Explore and ask</span>
          </div>
        </section>

        <section className="onboard-panel">
          <div className="onboard-panel__header">
            <div>
              <span className="onboard-panel__label">New workspace</span>
              <h2>Onboard a repository</h2>
            </div>
            <div className="onboard-panel__privacy">
              <span>◇</span> Public repositories
            </div>
          </div>

          <div className="onboard-panel__chat">
            <ChatPanel
              userId={session.userId}
              sessionId={session.sessionId}
              pageMode="home"
              applicationSlug={null}
              placeholder="Paste a public GitHub URL…"
              emptyText="Paste a repository URL below to begin the guided onboarding process."
              onToolResult={handleToolResult}
            />
          </div>
        </section>

        <section className="applications-panel">
          <div className="applications-panel__header">
            <div>
              <span className="section-kicker">Your knowledge base</span>
              <h2>Onboarded applications</h2>
              <p>Open a workspace to browse its documentation and ask questions.</p>
            </div>
            {!loadingApps && (
              <span className="applications-panel__count">
                {apps.length} {apps.length === 1 ? 'application' : 'applications'}
              </span>
            )}
          </div>

          <div className="applications-panel__content">
            {loadingApps ? (
              <div className="empty-state">
                <span className="empty-state__loader" />
                <p>Loading applications…</p>
              </div>
            ) : apps.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state__icon">⌘</div>
                <p>No applications yet</p>
                <span>Onboard your first public repository above.</span>
              </div>
            ) : (
              <div className="apps-grid">
                {apps.map(application => (
                  <ApplicationCard
                    key={application.applicationSlug}
                    app={application}
                  />
                ))}
              </div>
            )}
          </div>
        </section>
      </main>

      <footer className="home-footer">
        <span>DocWiki</span>
        <span>Code intelligence, made navigable.</span>
      </footer>
    </div>
  )
}
