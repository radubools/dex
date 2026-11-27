// The application bundle (app/app.js in the private files bucket). The shell
// imports it only after Firebase says the person is authorised, and calls
// `mount` with the signed-in session.
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import type { AppContext } from '../firebase'
import { App } from './App'
import { init } from './data'
import css from './styles.css?inline'

export function mount(el: HTMLElement, ctx: AppContext) {
  init(ctx)
  const style = document.createElement('style')
  style.textContent = css
  document.head.appendChild(style)
  const root = createRoot(el)
  root.render(
    <StrictMode>
      <App user={ctx.user} profile={ctx.profile} pollSeconds={ctx.config.pollSeconds ?? 15} />
    </StrictMode>,
  )
  return {
    unmount() {
      root.unmount()
      style.remove()
    },
  }
}
