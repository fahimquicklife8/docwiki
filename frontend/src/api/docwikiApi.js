// Keep this empty during local or Cloud Shell development.
// Vite proxies /api requests to the backend on port 8000.
//
// Set VITE_API_BASE_URL only when calling a deployed backend.

const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ?? ''
).replace(/\/+$/, '')

const USER_ID_KEY = 'docwiki_user_id'
const HOME_SESSION_KEY = 'docwiki_home_session_id'

function repositorySessionKey(applicationSlug) {
  return `docwiki_repo_session_id_${applicationSlug}`
}

function getSessionStorageKey(pageMode, applicationSlug) {
  if (pageMode === 'repository') {
    if (!applicationSlug) {
      throw new Error(
        'A repository session requires an application slug'
      )
    }

    return repositorySessionKey(applicationSlug)
  }

  return HOME_SESSION_KEY
}

async function readJson(response, fallbackMessage) {
  if (!response.ok) {
    let detail = ''

    try {
      detail = await response.text()
    } catch {
      // Ignore response parsing errors.
    }

    if (detail) {
      throw new Error(
        `${fallbackMessage}: ${response.status} ${detail}`
      )
    }

    throw new Error(
      `${fallbackMessage}: ${response.status}`
    )
  }

  return response.json()
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

export async function getOrCreateSession({
  pageMode = 'home',
  applicationSlug = null,
} = {}) {
  const sessionStorageKey = getSessionStorageKey(
    pageMode,
    applicationSlug
  )

  const storedUserId = localStorage.getItem(USER_ID_KEY)
  const storedSessionId = localStorage.getItem(
    sessionStorageKey
  )

  // Remove the old single-session key from the previous design.
  localStorage.removeItem('docwiki_session_id')

  if (storedUserId && storedSessionId) {
    let sessionExists = false

    try {
      const response = await fetch(
        `${API_BASE}/api/sessions/` +
        `${encodeURIComponent(storedUserId)}/` +
        `${encodeURIComponent(storedSessionId)}`
      )

      sessionExists = response.ok
    } catch {
      sessionExists = false
    }

    if (sessionExists) {
      await updateSessionContext({
        userId: storedUserId,
        sessionId: storedSessionId,
        pageMode,
        applicationSlug,
      })

      return {
        userId: storedUserId,
        sessionId: storedSessionId,
      }
    }

    localStorage.removeItem(sessionStorageKey)
  }

  const response = await fetch(
    `${API_BASE}/api/sessions`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        userId: storedUserId,
        pageMode,
        applicationSlug,
      }),
    }
  )

  const data = await readJson(
    response,
    'Failed to create chat session'
  )

  if (!data.userId || !data.sessionId) {
    throw new Error(
      'Session response did not include userId and sessionId'
    )
  }

  localStorage.setItem(USER_ID_KEY, data.userId)
  localStorage.setItem(
    sessionStorageKey,
    data.sessionId
  )

  return {
    userId: data.userId,
    sessionId: data.sessionId,
  }
}

export async function updateSessionContext({
  userId,
  sessionId,
  pageMode,
  applicationSlug = null,
}) {
  const response = await fetch(
    `${API_BASE}/api/sessions/context`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        userId,
        sessionId,
        pageMode,
        applicationSlug,
      }),
    }
  )

  return readJson(
    response,
    'Failed to update session context'
  )
}

// ---------------------------------------------------------------------------
// Applications
// ---------------------------------------------------------------------------

export async function listApplications() {
  const response = await fetch(
    `${API_BASE}/api/applications`
  )

  return readJson(
    response,
    'Failed to load applications'
  )
}

export async function getApplication(slug) {
  const safeSlug = encodeURIComponent(slug)

  const response = await fetch(
    `${API_BASE}/api/applications/${safeSlug}`
  )

  return readJson(
    response,
    'Application not found'
  )
}

export async function listDocuments(slug) {
  const safeSlug = encodeURIComponent(slug)

  const response = await fetch(
    `${API_BASE}/api/applications/` +
    `${safeSlug}/documents`
  )

  if (!response.ok) {
    return []
  }

  return response.json()
}

export async function getDocument(slug, docSlug) {
  const safeSlug = encodeURIComponent(slug)
  const safeDocSlug = encodeURIComponent(docSlug)

  const response = await fetch(
    `${API_BASE}/api/applications/` +
    `${safeSlug}/documents/${safeDocSlug}`
  )

  return readJson(
    response,
    'Document not found'
  )
}

// ---------------------------------------------------------------------------
// Chat streaming
// ---------------------------------------------------------------------------

export async function* streamChat(
  message,
  userId,
  sessionId,
  {
    pageMode = 'home',
    applicationSlug = null,
  } = {}
) {
  let response

  try {
    response = await fetch(
      `${API_BASE}/api/chat/stream`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message,
          userId,
          sessionId,
          pageMode,
          applicationSlug,
        }),
      }
    )
  } catch {
    yield {
      type: 'error',
      message: 'Failed to connect to the agent.',
    }
    return
  }

  if (!response.ok || !response.body) {
    let detail = ''

    try {
      detail = await response.text()
    } catch {
      // Ignore response parsing errors.
    }

    yield {
      type: 'error',
      message:
        detail ||
        `Failed to connect to the agent (${response.status}).`,
    }

    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()

  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()

      if (done) {
        buffer += decoder.decode()
        break
      }

      buffer += decoder.decode(
        value,
        { stream: true }
      )

      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''

      for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, '')

        if (!line.startsWith('data: ')) {
          continue
        }

        const payload = line.slice(6).trim()

        if (!payload) {
          continue
        }

        try {
          yield JSON.parse(payload)
        } catch {
          // Ignore malformed SSE events.
        }
      }
    }

    const finalLine = buffer.replace(/\r$/, '')

    if (finalLine.startsWith('data: ')) {
      const payload = finalLine.slice(6).trim()

      if (payload) {
        try {
          yield JSON.parse(payload)
        } catch {
          // Ignore incomplete final events.
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}