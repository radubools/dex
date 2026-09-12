import { useRef, useState } from 'react'

/**
 * The message box. Return submits — always, on touch as well as with a
 * keyboard — and Shift+Return inserts a newline.
 */
export function Composer({
  onSend,
  disabled,
  placeholder,
}: {
  onSend: (text: string) => void
  disabled?: boolean
  placeholder?: string
}) {
  const [draft, setDraft] = useState('')
  const field = useRef<HTMLTextAreaElement>(null)

  const submit = () => {
    const text = draft.trim()
    if (!text || disabled) return
    setDraft('')
    onSend(text)
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
      <button type="submit" className="send-btn" disabled={disabled || !draft.trim()}>
        Send
      </button>
    </form>
  )
}
