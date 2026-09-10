import ReactMarkdown from 'react-markdown'

export function MarkdownViewer({ content }) {
  if (!content) return null
  return (
    <div className="markdown">
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  )
}
