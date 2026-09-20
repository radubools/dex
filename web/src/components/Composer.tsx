import { useRef, useState } from 'react'
import { Attachments, namesOf, type Attached } from './Attachments'

/**
 * The message box. Return submits — always, on touch as well as with a
 * keyboard — and Shift+Return inserts a newline.
 */
export function Composer({
  onSend,
  disabled,
  placeholder,
  project,
}: {
  onSend: (text: string, uploads: string[]) => void
  disabled?: boolean
  placeholder?: string
  /** Whose `datasets/` directory attachments are stored in. */
  project?: string
}) {
  const [draft, setDraft] = useState('')
  const [attached, setAttached] = useState<Attached[]>([])
  const field = useRef<HTMLTextAreaElement>(null)

  const submit = () => {
    const text = draft.trim()
    if (!text || disabled) return
    setDraft('')
    setAttached([])
    onSend(text, namesOf(attached))
    field.current?.focus()
  }

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <textarea
        ref={field}
        value={draft}
        rows={1}
        enterKeyHint="send"
        placeholder={placeholder ?? 'Describe what you want generated…'}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          // Shift+Return is the only way to get a newline; plain Return sends.
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
      />
      <Attachments
        attached={attached}
        onChange={setAttached}
        project={project}
        disabled={disabled}
      />
      <button type="submit" className="send-btn" disabled={disabled || !draft.trim()}>
        Send
      </button>
    </form>
  )
}
