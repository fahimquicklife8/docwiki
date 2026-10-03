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
import { ResizableChat } from '../components/ResizableChat.jsx'

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
  const [headings, setHeadings] = useState([])
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

  // Load repository metadata and generated documentation.
  useEffect(() => {
    let cancelled = false

    setLoading(true)
    setMeta(null)
    setDocContent('')
    setHeadings([])
    setDocuments([])
    setActiveDocSlug(null)

    Promise.all([
      getApplication(appSlug),
      listDocuments(appSlug),
    ])
      .then(([applicationMeta, availableDocuments]) => {
        if (cancelled) return

        setMeta(applicationMeta)

        const orderedDocuments = [...availableDocuments].sort((a, b) =>
          Number(b.kind === 'overview') - Number(a.kind === 'overview')
        )
        setDocuments(orderedDocuments)
        setActiveDocSlug(orderedDocuments[0]?.slug || null)
      })
      .catch(error => {
        if (cancelled) return

        console.error(
          'Failed to load repository documentation:',
          error
        )

        setDocContent(
          '_Documentation is not available._'
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
    setHeadings([])

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
          'Failed to load document:',
          error
        )

        setDocContent(
          '_Documentation is not available._'
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
          headings={headings}
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

          <div className="repository-facts" aria-label="Repository analysis">
            {meta?.languages?.length > 0 && <span>Analyzed languages: {meta.languages.join(', ')}</span>}
            {[
              ['sourceFiles', 'source files'], ['graphNodes', 'graph nodes'],
              ['graphEdges', 'static relationships'],
            ].map(([key, label]) => Number.isFinite(meta?.statistics?.[key]) && (
              <span key={key}>{meta.statistics[key].toLocaleString()} {label}</span>
            ))}
            {documents.length > 0 && <span>{documents.length} generated documents</span>}
          </div>
          <div className="doc-content__rule" />

          {loading ? (
            <div className="document-loading">
              <span className="empty-state__loader" />
              <p>Loading documentation…</p>
            </div>
          ) : docContent ? (
            <MarkdownViewer
              content={docContent}
              onHeadings={setHeadings}
            />
          ) : (
            <div className="document-empty">
              <span>◇</span>
              <p>Documentation has not been generated yet.</p>
            </div>
          )}
        </div>

        <ResizableChat key={appSlug} heading={<>
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
          </>}>

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
        </ResizableChat>
      </div>
    </div>
  )
}
