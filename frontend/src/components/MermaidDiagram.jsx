import { useEffect, useRef, useState } from 'react'
import { useMermaid } from '../hooks/useMermaid.js'

export function MermaidDiagram({ source }) {
  const result = useMermaid(source)
  const [showSource, setShowSource] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [expanded, setExpanded] = useState(false)
  const [fitWidth, setFitWidth] = useState(null)
  const dialog = useRef(null)
  const viewport = useRef(null)
  const drag = useRef(null)
  const expandButton = useRef(null)

  useEffect(() => {
    setZoom(1)
    setShowSource(false)
  }, [source])

  useEffect(() => {
    if (expanded) dialog.current?.showModal()
    else if (dialog.current?.open) {
      dialog.current.close()
      expandButton.current?.focus()
    }
  }, [expanded])

  useEffect(() => {
    const element = viewport.current
    if (!element || !result?.width || !result?.height) return
    const fit = () => {
      const style = getComputedStyle(element)
      const availableWidth = element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
      const availableHeight = parseFloat(style.maxHeight) - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)
      // Fit both axes and avoid enlarging small diagrams until the user zooms.
      setFitWidth(Math.max(1, Math.min(availableWidth, availableHeight * result.width / result.height, result.width)))
    }
    const observer = new ResizeObserver(fit)
    observer.observe(element)
    window.addEventListener('resize', fit)
    fit()
    return () => { observer.disconnect(); window.removeEventListener('resize', fit) }
  }, [result, expanded, showSource])

  function resetView() {
    setZoom(1)
    viewport.current?.scrollTo({ left: 0, top: 0 })
  }

  const content = (
    <section className="mermaid-diagram" aria-label="Mermaid diagram">
      <div className="mermaid-diagram__toolbar">
        <span className="mermaid-diagram__title"><span aria-hidden="true">◇</span> Architecture diagram</span>
        <div>
          <button type="button" title="Zoom out" aria-label="Zoom out diagram" disabled={zoom <= 0.5 || showSource} onClick={() => setZoom(value => Math.max(0.5, value - 0.25))}>−</button>
          <button type="button" title="Fit diagram" aria-label="Reset diagram zoom" onClick={resetView}>{Math.round(zoom * 100)}%</button>
          <button type="button" title="Zoom in" aria-label="Zoom in diagram" disabled={zoom >= 3 || showSource} onClick={() => setZoom(value => Math.min(3, value + 0.25))}>+</button>
          <span className="mermaid-diagram__divider" />
          <button type="button" aria-pressed={showSource} onClick={() => setShowSource(value => !value)}>{showSource ? 'Diagram' : 'Source'}</button>
          <button ref={expanded ? null : expandButton} type="button" aria-label={expanded ? 'Close expanded diagram' : 'Expand diagram'} title={expanded ? 'Close (Escape)' : 'Expand diagram'} onClick={() => setExpanded(value => !value)}>{expanded ? '×' : '⤢'}</button>
        </div>
      </div>
      {showSource || result?.error ? (
        <>
          {result?.error && <p role="status">This diagram could not be rendered. Its source is shown below.</p>}
          <pre><code className="language-mermaid">{result?.renderedSource || source}</code></pre>
        </>
      ) : result?.svg ? (
        <div className="mermaid-diagram__viewport" ref={viewport} tabIndex={0} aria-label="Diagram canvas. Scroll or drag to pan."
          onPointerDown={event => {
            if (event.button !== 0 || event.pointerType !== 'mouse') return
            const element = event.currentTarget
            drag.current = { x: event.clientX, y: event.clientY, left: element.scrollLeft, top: element.scrollTop }
            element.setPointerCapture(event.pointerId)
          }}
          onPointerMove={event => {
            if (!drag.current) return
            event.currentTarget.scrollLeft = drag.current.left - (event.clientX - drag.current.x)
            event.currentTarget.scrollTop = drag.current.top - (event.clientY - drag.current.y)
          }}
          onPointerUp={() => { drag.current = null }}
          onPointerCancel={() => { drag.current = null }}
          onLostPointerCapture={() => { drag.current = null }}>
          <div className="mermaid-diagram__canvas" style={{ width: fitWidth ? `${fitWidth * zoom}px` : `${zoom * 100}%` }} dangerouslySetInnerHTML={{ __html: result.svg }} />
        </div>
      ) : <div className="mermaid-diagram__loading" role="status"><span className="empty-state__loader" />Rendering diagram…</div>}
      <div className="mermaid-diagram__footer"><span className="mermaid-diagram__status" />{showSource || result?.error ? 'Mermaid source' : 'Scroll or drag to explore'}<span>Mermaid</span></div>
    </section>
  )

  return <>
    {!expanded && content}
    <dialog className="mermaid-diagram__dialog" ref={dialog} aria-label="Expanded architecture diagram" onCancel={() => setExpanded(false)} onClose={() => { setExpanded(false); expandButton.current?.focus() }}>
      {expanded && content}
    </dialog>
  </>
}
