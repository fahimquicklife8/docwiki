import {
  useEffect,
  useState,
} from 'react'

import {
  Link,
  useParams,
} from 'react-router-dom'

import { ChatPanel } from '../components/ChatPanel.jsx'
import { DocumentationSidebar } from '../components/DocumentationSidebar.jsx'
import { MarkdownViewer } from '../components/MarkdownViewer.jsx'

import {
  getApplication,
  getDocument,
  getOrCreateSession,
  listDocuments,
} from '../api/docwikiApi.js'

export function ApplicationPage() {
  const { appSlug } = useParams()

  const [session, setSession] = useState({
    userId: null,
    sessionId: null,
  })

  const [meta, setMeta] = useState(null)
  const [documents, setDocuments] = useState([])
  const [activeDocSlug, setActiveDocSlug] = useState(null)
  const [docContent, setDocContent] = useState('')
  const [activated, setActivated] = useState(false)
  const [loading, setLoading] = useState(true)

  // Create or restore a session dedicated to this repository.
  useEffect(() => {
    let cancelled = false

    setActivated(false)
    setSession({
      userId: null,
      sessionId: null,
    })

    getOrCreateSession({
      pageMode: 'repository',
      applicationSlug: appSlug,
    })
      .then(currentSession => {
        if (cancelled) return

        setSession(currentSession)
        setActivated(true)
      })
      .catch(error => {
        if (cancelled) return

        console.error(
          'Repository session creation failed:',
          error
        )

        setActivated(false)
      })

    return () => {
      cancelled = true
    }
  }, [appSlug])

  // Load repository metadata and overview.md.
  useEffect(() => {
    let cancelled = false

    setLoading(true)
    setMeta(null)
    setDocContent('')
    setDocuments([])
    setActiveDocSlug(null)

    Promise.all([
      getApplication(appSlug),
      listDocuments(appSlug),
    ])
      .then(([applicationMeta, availableDocuments]) => {
        if (cancelled) return

        setMeta(applicationMeta)

        const overviewDocument = (
          availableDocuments.find(document => {
            const slug = String(
              document.slug || ''
            ).toLowerCase()

            const name = String(
              document.name ||
              document.filename ||
              ''
            ).toLowerCase()

            const path = String(
              document.path || ''
            ).toLowerCase()

            return (
              slug === 'overview' ||
              slug === 'overview.md' ||
              name === 'overview' ||
              name === 'overview.md' ||
              path === 'docs/overview.md' ||
              path.endsWith('/overview.md')
            )
          })
        )

        if (!overviewDocument) {
          setDocContent(
            '_Overview documentation is not available._'
          )
          return
        }

        setDocuments([overviewDocument])
        setActiveDocSlug(
          overviewDocument.slug
        )
      })
      .catch(error => {
        if (cancelled) return

        console.error(
          'Failed to load repository overview:',
          error
        )

        setDocContent(
          '_Overview documentation is not available._'
        )
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [appSlug])

  useEffect(() => {
    if (!activeDocSlug) {
      return undefined
    }

    let cancelled = false

    setDocContent('')

    getDocument(
      appSlug,
      activeDocSlug
    )
      .then(document => {
        if (!cancelled) {
          setDocContent(document.content)
        }
      })
      .catch(error => {
        if (cancelled) return

        console.error(
          'Failed to load overview document:',
          error
        )

        setDocContent(
          '_Overview documentation is not available._'
        )
      })

    return () => {
      cancelled = true
    }
  }, [appSlug, activeDocSlug])

  return (
    <div className="app-page">
      <header className="header">
        <div className="header__brand">
          <div className="header__logo">D</div>
          <div>
            <div className="header__title">DocWiki</div>
            <div className="header__tagline">Engineering intelligence</div>
          </div>
        </div>

        <Link
          to="/"
          className="header__back"
        >
          <span aria-hidden="true">←</span>
          All applications
        </Link>

        {meta && (
          <div className="header__repository">
            <span className="header__repository-name">{meta.displayName}</span>
            <span className="header__repository-divider" />
            <span className="header__branch">
              <span aria-hidden="true">⑂</span>
              {meta.resolvedBranch || 'default'}
            </span>
          </div>
        )}
      </header>

      <div className="app-page-body">
        <DocumentationSidebar
          documents={documents}
          activeSlug={activeDocSlug}
          onSelect={setActiveDocSlug}
        />

        <div className="doc-content">
          <div className="doc-content__header">
            <div>
              <span className="section-kicker">Repository documentation</span>
              <h1>{meta?.displayName || appSlug}</h1>
            </div>
            {meta?.status && (
              <span className={`status-badge status-badge--${meta.status}`}>
                <span className="status-badge__dot" />
                {meta.status}
              </span>
            )}
          </div>

          <div className="doc-content__rule" />

          {loading ? (
            <div className="document-loading">
              <span className="empty-state__loader" />
              <p>Loading overview…</p>
            </div>
          ) : docContent ? (
            <MarkdownViewer
              content={docContent}
            />
          ) : (
            <div className="document-empty">
              <span>◇</span>
              <p>Overview documentation has not been generated yet.</p>
            </div>
          )}
        </div>

        <div className="app-chat-panel">
          <div className="app-chat-panel__title">
            <div className="app-chat-panel__heading">
              <span className="app-chat-panel__spark">✦</span>
              <div>
                <strong>Repository assistant</strong>
                <span>
                  {activated ? 'Ready for questions' : 'Preparing context…'}
                </span>
              </div>
            </div>
            <span
              className={`app-chat-panel__dot${
                activated ? '' : ' app-chat-panel__dot--inactive'
              }`}
            />
          </div>

          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            <ChatPanel
              key={appSlug}
              userId={session.userId}
              sessionId={session.sessionId}
              pageMode="repository"
              applicationSlug={appSlug}
              placeholder={
                activated
                  ? 'Ask about this codebase…'
                  : 'Preparing repository chat…'
              }
              className="chat-panel--app"
              emptyText={
                activated
                  ? 'Ask anything about this repository.'
                  : 'Preparing repository context…'
              }
            />
          </div>
        </div>
      </div>
    </div>
  )
}
