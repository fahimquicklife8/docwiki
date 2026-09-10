import {
  useEffect,
  useRef,
  useState,
} from 'react'

import { streamChat } from '../api/docwikiApi.js'

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
  const bottomRef = useRef(null)

  const sessionReady = Boolean(
    userId &&
    sessionId &&
    (
      pageMode === 'home' ||
      applicationSlug
    )
  )

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: 'smooth',
    })
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
          event.type === 'text' &&
          !event.partial
        ) {
          const agentMessage = {
            role: 'agent',
            content: event.content,
          }

          setMessages(previous => {
            const last = previous[
              previous.length - 1
            ]

            if (last?.role === 'agent') {
              return [
                ...previous.slice(0, -1),
                agentMessage,
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
      <div className="chat-messages">
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

        {messages.map((message, index) => (
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
          ) : (
            <div
              key={index}
              className={`chat-message chat-message--${message.role}`}
            >
              {message.content}
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

        <div ref={bottomRef} />
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
