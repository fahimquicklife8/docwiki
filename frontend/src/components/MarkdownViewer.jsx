import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useEffect, useId, useRef } from 'react'
import { MermaidDiagram } from './MermaidDiagram.jsx'

const components = {
  pre({ children, node, ...props }) {
    const code = node?.children?.[0]
    const source = code?.children?.map(child => child.value || '').join('').trim() || ''
    const languages = code?.properties?.className || []
    const isMermaid = languages.some(name => /^language-mermaid$/i.test(name))
      // Generated answers occasionally mislabel a Mermaid fence as sql/text.
      // Recognize an unambiguous diagram declaration, regardless of that label.
      || /^(?:(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\b|(?:sequenceDiagram|classDiagram|stateDiagram-v2|erDiagram)(?:\s|$))/.test(source)
    if (code?.tagName === 'code' && isMermaid) {
      return <MermaidDiagram source={source} />
    }
    return <pre {...props}>{children}</pre>
  },
}

export function MarkdownViewer({ content, onHeadings }) {
  const container = useRef(null)
  const prefix = useId()
  useEffect(() => {
    if (!onHeadings) return
    const headings = Array.from(container.current?.querySelectorAll('h1,h2,h3,h4,h5,h6') || [])
      .map((heading, index) => {
        heading.id = `${prefix}-heading-${index}`
        return { id: heading.id, title: heading.textContent, depth: Number(heading.tagName[1]) }
      })
    onHeadings(headings)
  }, [content, onHeadings, prefix])
  if (!content) return null
  return (
    <div className="markdown" ref={container}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{content}</ReactMarkdown>
    </div>
  )
}
