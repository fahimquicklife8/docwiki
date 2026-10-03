import { useEffect, useId, useState } from 'react'

let renderer
let renderSequence = 0
const cache = new Map()

// Only accept a repair when Mermaid itself validates it. A flowchart has no
// closing `end`; models sometimes add one after the final subgraph has closed.
async function parseSource(mermaid, source) {
  if (await mermaid.parse(source, { suppressErrors: true })) return source
  if (!/^\s*(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\b/.test(source)) {
    throw new Error('Invalid Mermaid syntax')
  }
  let candidate = source.trimEnd()
  for (let attempts = 0; attempts < 3; attempts += 1) {
    const shorter = candidate.replace(/(?:^|\r?\n)[\t ]*end[\t ]*;?[\t ]*$/, '').trimEnd()
    if (shorter === candidate) break
    candidate = shorter
    if (await mermaid.parse(candidate, { suppressErrors: true })) return candidate
  }
  throw new Error('Invalid Mermaid syntax')
}

function getRenderer() {
  renderer ||= import('mermaid').then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      suppressErrorRendering: true,
      theme: 'base',
      look: 'neo',
      fontFamily: 'Inter, system-ui, sans-serif',
      themeVariables: {
        darkMode: true,
        background: '#101521',
        primaryColor: '#252c49',
        primaryTextColor: '#f1f3ff',
        primaryBorderColor: '#9397e8',
        secondaryColor: '#173b42',
        secondaryTextColor: '#d8fbf2',
        secondaryBorderColor: '#6abeb1',
        tertiaryColor: '#192235',
        tertiaryTextColor: '#dbe7ff',
        tertiaryBorderColor: '#506687',
        lineColor: '#9caed0',
        textColor: '#e5ecff',
        edgeLabelBackground: '#151c2c',
        clusterBkg: '#141c2b',
        clusterBorder: '#3c4964',
        fontSize: '14px',
      },
      flowchart: { curve: 'basis', nodeSpacing: 40, rankSpacing: 64, padding: 20, htmlLabels: false },
    })
    return mermaid
  }).catch(error => { renderer = undefined; throw error })
  return renderer
}

export function useMermaid(source) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const [result, setResult] = useState(null)
  useEffect(() => {
    let cancelled = false
    // Wait for streaming Markdown to settle and ignore results after navigation.
    const timer = setTimeout(async () => {
      try {
        const mermaid = await getRenderer()
        if (cancelled) return
        const key = `${id}:${source}`
        let diagram = cache.get(key)
        if (!diagram) {
          const renderedSource = await parseSource(mermaid, source)
          if (cancelled) return
          const rendered = await mermaid.render(`diagram-${id}-${++renderSequence}`, renderedSource)
          diagram = { svg: rendered.svg, renderedSource }
          cache.set(key, diagram)
          if (cache.size > 24) cache.delete(cache.keys().next().value)
        }
        if (!cancelled) {
          const element = new DOMParser().parseFromString(diagram.svg, 'image/svg+xml').documentElement
          const viewBox = element.getAttribute('viewBox')?.split(/[\s,]+/).map(Number)
          setResult({ source, ...diagram, width: viewBox?.[2], height: viewBox?.[3] })
        }
      } catch {
        if (!cancelled) setResult({ source, error: true })
      }
    }, 180)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [source, id])
  return result?.source === source ? result : null
}
