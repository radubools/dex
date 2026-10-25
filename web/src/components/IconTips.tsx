import { useEffect } from 'react'

/**
 * Names for icon-only buttons, the moment they are needed. A browser shows a
 * `title` only after a second's hover, and never on a phone or tablet, so an
 * icon (☰, 🕸, 📖, ⚙) said nothing there. This shows the button's `title` or
 * `aria-label` in a small bubble: at once under a pointer, for a moment when
 * a finger taps it, and while it has keyboard focus. Buttons with words in
 * them are left alone. Mounted once, for the whole app.
 */

// Visible text with no letters or digits: an icon (emoji, symbol) or nothing.
const iconOnly = (el: Element) => !/[\p{L}\p{N}]/u.test(el.textContent ?? '')

function labelOf(el: HTMLElement): string {
  // Moved out of `title` so the browser's own late tooltip does not show as well.
  const title = el.getAttribute('title')
  if (title) {
    el.dataset.tip = title
    el.removeAttribute('title')
  }
  return el.dataset.tip || el.getAttribute('aria-label') || ''
}

function target(e: Event): HTMLElement | null {
  const el = (e.target as Element | null)?.closest?.('button, [role="button"], [role="tab"], a')
  return el instanceof HTMLElement && iconOnly(el) ? el : null
}

export function IconTips() {
  useEffect(() => {
    const tip = document.createElement('div')
    tip.className = 'icon-tip'
    tip.setAttribute('role', 'tooltip')
    tip.hidden = true
    document.body.appendChild(tip)
    let owner: HTMLElement | null = null
    let timer = 0

    const hide = () => {
      tip.hidden = true
      owner = null
      window.clearTimeout(timer)
    }
    const show = (el: HTMLElement) => {
      const text = labelOf(el)
      if (!text) return hide()
      owner = el
      tip.textContent = text
      tip.hidden = false
      // Under the button, inside the window; above it when there is no room below.
      const r = el.getBoundingClientRect()
      const t = tip.getBoundingClientRect()
      const left = Math.min(Math.max(6, r.left + r.width / 2 - t.width / 2), window.innerWidth - t.width - 6)
      const below = r.bottom + 6 + t.height < window.innerHeight
      tip.style.left = `${Math.round(left)}px`
      tip.style.top = `${Math.round(below ? r.bottom + 6 : r.top - 6 - t.height)}px`
    }

    const over = (e: PointerEvent) => {
      if (e.pointerType === 'touch') return
      const el = target(e)
      if (el && el !== owner) show(el)
      else if (!el && owner) hide()
    }
    const down = (e: PointerEvent) => {
      const el = target(e)
      if (e.pointerType !== 'touch' || !el) { if (e.pointerType !== 'touch') hide(); return }
      show(el)
      window.clearTimeout(timer)
      timer = window.setTimeout(hide, 1600)
    }
    const focus = (e: FocusEvent) => {
      const el = target(e)
      if (el && (e.target as HTMLElement).matches?.(':focus-visible')) show(el)
    }
    document.addEventListener('pointerover', over)
    document.addEventListener('pointerdown', down, true)
    document.addEventListener('focusin', focus)
    document.addEventListener('focusout', hide)
    window.addEventListener('scroll', hide, true)
    window.addEventListener('blur', hide)
    return () => {
      document.removeEventListener('pointerover', over)
      document.removeEventListener('pointerdown', down, true)
      document.removeEventListener('focusin', focus)
      document.removeEventListener('focusout', hide)
      window.removeEventListener('scroll', hide, true)
      window.removeEventListener('blur', hide)
      window.clearTimeout(timer)
      tip.remove()
    }
  }, [])
  return null
}
