import { useEffect, useRef, useState } from 'react'

export function ResizableChat({ heading, children }) {
  const panel = useRef(null)
  const drag = useRef(null)
  const [width, setWidth] = useState(null)
  const [fullscreen, setFullscreen] = useState(false)
  const clamp = value => Math.min(Math.max(315, value), window.innerWidth * 0.7)

  useEffect(() => {
    const resize = () => setWidth(value => value === null ? null : clamp(value))
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [])

  useEffect(() => {
    if (!fullscreen) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const escape = event => { if (event.key === 'Escape') setFullscreen(false) }
    window.addEventListener('keydown', escape)
    return () => {
      document.body.style.overflow = previous
      window.removeEventListener('keydown', escape)
    }
  }, [fullscreen])

  return (
    <div ref={panel} className={`app-chat-panel${fullscreen ? ' app-chat-panel--fullscreen' : ''}`}
      style={width === null ? undefined : { '--chat-width': `${width}px` }}>
      {!fullscreen && <div className="app-chat-panel__resize" role="separator" aria-label="Resize chat width"
        aria-orientation="vertical" aria-valuemin={315} aria-valuemax={Math.round(window.innerWidth * 0.7)}
        aria-valuenow={Math.round(width ?? (window.innerWidth <= 1050 ? 315 : 350))} tabIndex={0}
        onPointerDown={event => {
          if (event.button !== 0) return
          drag.current = { x: event.clientX, width: panel.current.getBoundingClientRect().width }
          event.currentTarget.setPointerCapture(event.pointerId)
          event.preventDefault()
        }}
        onPointerMove={event => {
          if (drag.current) setWidth(clamp(drag.current.width + drag.current.x - event.clientX))
        }}
        onPointerUp={() => { drag.current = null }}
        onPointerCancel={() => { drag.current = null }}
        onLostPointerCapture={() => { drag.current = null }}
        onDoubleClick={() => setWidth(null)}
        onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return
          event.preventDefault()
          setWidth(event.key === 'Home' ? null : clamp(panel.current.getBoundingClientRect().width + (event.key === 'ArrowLeft' ? 40 : -40)))
        }} />}
      <div className="app-chat-panel__title">
        {heading}
        <div className="app-chat-panel__controls">
          {!fullscreen && <button type="button" className="chat-width-toggle" title={width === null ? 'Widen chat' : 'Reset chat width'} aria-label={width === null ? 'Widen chat' : 'Reset chat width'}
            onClick={() => setWidth(width === null ? clamp(600) : null)}>↔</button>}
          <button type="button" title={fullscreen ? 'Restore chat (Esc)' : 'Fullscreen chat'} aria-label={fullscreen ? 'Restore chat' : 'Fullscreen chat'} aria-pressed={fullscreen}
            onClick={() => setFullscreen(value => !value)}>
            <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
              {fullscreen ? <><path d="M6 6V3h11v11h-3" /><rect x="3" y="6" width="11" height="11" rx="1" /></> : <rect x="3" y="3" width="14" height="14" rx="1" />}
            </svg>
          </button>
        </div>
      </div>
      {children}
    </div>
  )
}
