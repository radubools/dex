import { useRef, useState } from 'react'
import { uploadSources } from '../api'
import type { UploadBatch } from '../types'

/** One attached file, named as the project's data directory has it. */
export type Attached = { name: string; bytes: number }

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

/**
 * The `+` button and the list of what it has attached.
 *
 * Files upload as soon as they are chosen rather than on send: an upload that
 * only happened at send time would make sending slow and silent for a large
 * PDF, and there would be no moment to show that it failed. By the time the
 * message goes, the server already holds them and the message carries ids.
 */
export function Attachments({
  attached,
  onChange,
  project,
  disabled,
}: {
  attached: Attached[]
  onChange: (next: Attached[]) => void
  /** Whose `datasets/` directory the files go into. */
  project?: string
  disabled?: boolean
}) {
  const picker = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const take = async (files: FileList | null) => {
    if (!files || !files.length) return
    setBusy(true)
    setError(null)
    try {
      const stored: UploadBatch = await uploadSources([...files], project)
      onChange([
        ...attached,
        // The stored name, not the chosen one: a clash is kept rather than
        // overwritten, so `notes.md` can land as `notes-2.md`.
        ...stored.files.map((f) => ({ name: f.name, bytes: f.bytes })),
      ])
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
      // Cleared so choosing the same file twice in a row still fires `change`.
      if (picker.current) picker.current.value = ''
    }
  }

  return (
    <>
      <input
        ref={picker}
        type="file"
        multiple
        hidden
        onChange={(e) => void take(e.target.files)}
      />
      <button
        type="button"
        className="attach-btn"
        title="Attach files as sources"
        aria-label="Attach files as sources"
        disabled={disabled || busy}
        onClick={() => picker.current?.click()}
      >
        {busy ? '…' : '+'}
      </button>
      {(attached.length > 0 || error) && (
        <div className="attached">
          {attached.map((file, i) => (
            <span className="attached-file" key={`${file.name}-${i}`}>
              {file.name}
              <span className="attached-size">{humanSize(file.bytes)}</span>
              <button
                type="button"
                className="attached-drop"
                aria-label={`Remove ${file.name}`}
                // Only from this message. The file stays in the project's
                // data directory: it is source material now, and another
                // message — or another task — may still want it.
                onClick={() => onChange(attached.filter((_, at) => at !== i))}
              >
                ×
              </button>
            </span>
          ))}
          {error && <span className="attached-error">{error}</span>}
        </div>
      )}
    </>
  )
}

/** The file names to send with a message, each once. */
export function namesOf(attached: Attached[]): string[] {
  return [...new Set(attached.map((f) => f.name))]
}
