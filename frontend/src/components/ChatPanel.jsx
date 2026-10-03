import {
  useEffect,
  useRef,
  useState,
} from 'react'

import { streamChat } from '../api/docwikiApi.js'
import { MarkdownViewer } from './MarkdownViewer.jsx'

function uniqueTools(tools) {
  const byName = new Map()
  for (const tool of tools) {
    const existing = byName.get(tool.name)
    if (!existing) {
      byName.set(tool.name, { ...tool })
    } else if (tool.status === 'running') {
      // Keep one stable row, active while any invocation is still running.
      existing.status = 'running'
    }
  }
  return Array.from(byName.values())
}

export function ChatPanel({
  userId,
  sessionId,
  pageMode = 'home',
  applicationSlug = null,
  placeholder = 'Type a message…',
  onToolResult,
  className = 'chat-panel--home',
  emptyText = (
    'Ask a question or paste a GitHub URL to get started.'
  ),
}) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const activeTools = []
  const messagesRef = useRef(null)
  // Deduplicate presentation only; retain every streamed event and callback.
  const visibleMessages = messages.map(message => (
    message.role === 'trace'
      ? { ...message, tools: uniqueTools(message.tools) }
      : message
  ))

  const sessionReady = Boolean(
    userId &&
    sessionId &&
    (
      pageMode === 'home' ||
      applicationSlug
    )
  )

  useEffect(() => {
    const container = messagesRef.current
    if (container) {
      // Scroll only the chat history, never its ancestors or the document.
      container.scrollTop = container.scrollHeight
    }
  }, [messages, streaming])

  // Clear visible messages if the underlying ADK session changes.
  useEffect(() => {
    setMessages([])
    setInput('')
    setStreaming(false)
  }, [sessionId])

  const send = async () => {
    const text = input.trim()

    if (
      !text ||
      streaming ||
      !sessionReady
    ) {
      return
    }

    const traceId = `trace-${Date.now()}`

    setMessages(previous => [
      ...previous,
      {
        role: 'user',
        content: text,
      },
      {
        role: 'trace',
        id: traceId,
        tools: [],
        pending: true,
      },
    ])

    setInput('')
    setStreaming(true)

    try {
      for await (
        const event of streamChat(
          text,
          userId,
          sessionId,
          {
            pageMode,
            applicationSlug,
          }
        )
      ) {
        if (
          event.type === 'text'
        ) {
          const agentMessage = {
            role: 'agent',
            content: event.content,
            partial: Boolean(event.partial),
          }

          setMessages(previous => {
            const last = previous[
              previous.length - 1
            ]

            if (last?.role === 'agent' && last.partial) {
              return [
                ...previous.slice(0, -1),
                { ...agentMessage, content: event.partial
                  ? last.content + event.content
                  : event.content },
              ]
            }

            return [
              ...previous,
              agentMessage,
            ]
          })
        } else if (
          event.type === 'tool_call'
        ) {
          setMessages(previous => previous.map(message => (
            message.id === traceId
              ? {
                  ...message,
                  tools: [
                    ...message.tools,
                    {
                      id: `${traceId}-${message.tools.length}`,
                      name: event.name,
                      status: 'running',
                    },
                  ],
                }
              : message
          )))
        } else if (
          event.type === 'tool_result'
        ) {
          setMessages(previous => previous.map(message => {
            if (message.id !== traceId) return message

            const toolIndex = message.tools.findIndex(
              tool => tool.name === event.name && tool.status === 'running'
            )
            const tools = [...message.tools]

            if (toolIndex >= 0) {
              tools[toolIndex] = {
                ...tools[toolIndex],
                status: 'done',
              }
            } else {
              tools.push({
                id: `${traceId}-${tools.length}`,
                name: event.name,
                status: 'done',
              })
            }

            return { ...message, tools }
          }))

          onToolResult?.(
            event.name,
            event.result
          )
        } else if (
          event.type === 'error'
        ) {
          setMessages(previous => [
            ...previous,
            {
              role: 'error',
              content: event.message,
            },
          ])
        } else if (
          event.type === 'done'
        ) {
          setMessages(previous => [
            ...previous,
            { role: 'usage', id: `${traceId}-usage`, usage: event.usage },
          ])
          break
        }
      }
    } catch (error) {
      console.error('Chat error:', error)

      setMessages(previous => [
        ...previous,
        {
          role: 'error',
          content: (
            'Connection error. Please try again.'
          ),
        },
      ])
    } finally {
      setMessages(previous => previous.map(message => (
        message.id === traceId
          ? {
              ...message,
              pending: false,
              tools: message.tools.map(tool => (
                tool.status === 'running'
                  ? { ...tool, status: 'done' }
                  : tool
              )),
            }
          : message
      )))
      // A disconnected stream may end before ADK's usage summary arrives.
      setMessages(previous => previous.some(message => message.id === `${traceId}-usage`)
        ? previous
        : [...previous, { role: 'usage', id: `${traceId}-usage`, usage: null }])
      setStreaming(false)
    }
  }

  const onKeyDown = event => {
    if (
      event.key === 'Enter' &&
      !event.shiftKey
    ) {
      event.preventDefault()
      send()
    }
  }

  return (
    <div className={`chat-panel ${className}`}>
      <div className="chat-messages" ref={messagesRef}>
        {messages.length === 0 && (
          <div
            style={{
              color: 'var(--text-muted)',
              fontSize: 13,
              textAlign: 'center',
              marginTop: 40,
            }}
          >
            {emptyText}
          </div>
        )}

        {visibleMessages.map((message, index) => (
          message.role === 'trace' ? (
            message.tools.length > 0 && (
              <div className="chat-tools" key={message.id}>
                <div className="chat-tools__title">
                  <span>{message.pending ? 'Working' : 'Activity trace'}</span>
                  <span>
                    {message.tools.filter(tool => tool.status === 'done').length}
                    /{message.tools.length}
                  </span>
                </div>
                {message.tools.map((tool, toolIndex) => (
                  <div
                    key={tool.id}
                    className={`chat-tool chat-tool--${tool.status}`}
                  >
                    <span className="chat-tool__status">
                      {tool.status === 'done' ? '✓' : ''}
                    </span>
                    <span className="chat-tool__name">
                      {tool.status === 'running' ? 'Running ' : 'Ran '}
                      {tool.name.replace(/_/g, ' ')}
                    </span>
                    <span className="chat-tool__number">{toolIndex + 1}</span>
                    {tool.status === 'running' && (
                      <span className="chat-tool__spinner" />
                    )}
                  </div>
                ))}
              </div>
            )
          ) : message.role === 'usage' ? (
            <div className="chat-token-usage" key={message.id}
              title="ADK-reported usage across all model calls for this request, including tool steps and sub-agents. Cached and thinking tokens are included in the reported total.">
              {message.usage?.totalTokens != null
                ? `${message.usage.totalTokens.toLocaleString()} tokens used${message.usage.incomplete ? ' (partial usage)' : ''}`
                : 'Token usage unavailable'}
            </div>
          ) : (
            <div
              key={index}
              className={`chat-message chat-message--${message.role}`}
            >
              {message.role === 'agent'
                ? <MarkdownViewer content={message.content} />
                : message.content}
            </div>
          )
        ))}

        {activeTools.length > 0 && (
          <div className="chat-tools">
            {activeTools.map((tool, index) => (
              <div
                key={`${tool.name}-${index}`}
                className={
                  `chat-tool ` +
                  `chat-tool--${tool.status}`
                }
              >
                <span>⚙</span>

                <span className="chat-tool__name">
                  {tool.name.replace(/_/g, ' ')}
                </span>

                {tool.status === 'running' && (
                  <span className="chat-tool__spinner" />
                )}

                {tool.status === 'done' && (
                  <span
                    style={{
                      color: 'var(--success)',
                    }}
                  >
                    ✓
                  </span>
                )}
              </div>
            ))}
          </div>
        )}

        {streaming &&
          activeTools.every(
            tool => tool.status === 'done'
          ) && (
            <div className="chat-typing">
              <span />
              <span />
              <span />
            </div>
          )}

      </div>

      <div className="chat-input-row">
        <input
          className="chat-input"
          value={input}
          onChange={event =>
            setInput(event.target.value)
          }
          onKeyDown={onKeyDown}
          placeholder={
            sessionReady
              ? placeholder
              : 'Preparing chat session…'
          }
          disabled={
            streaming ||
            !sessionReady
          }
        />

        <button
          className="chat-send"
          onClick={send}
          disabled={
            streaming ||
            !input.trim() ||
            !sessionReady
          }
        >
          {streaming ? '…' : 'Send'}
        </button>
      </div>
    </div>
  )
}
