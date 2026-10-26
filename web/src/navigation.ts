import type { ViewerTarget } from './components/Viewer'

/**
 * Navigation history: every pane the reader opens is a step the back button
 * walks through, in order, whether it is the browser's back or a ‹ in a pane.
 *
 * A *place* is everything that says what is on screen: the project, thread
 * and tab (also in the URL, so a link or a reload lands there), the task
 * panel, the viewer, and which full-screen pane (costs, guide, users, skills)
 * is over them. Each place a reader navigates to is pushed onto the
 * browser's history with the place in `history.state`; going back restores
 * the place before it. Changes the app makes by itself (picking a default
 * project or thread, following a task's follow-up) replace the current entry
 * instead, so back never stops on a place the reader did not choose.
 */

export type View = 'chat' | 'feed' | 'library' | 'graph'
export type Overlay = 'costs' | 'guide' | 'users' | 'skills' | null

export interface Place {
  project: string | null
  thread: string | null
  view: View
  task: string | null
  viewing: ViewerTarget | null
  overlay: Overlay
}

/** What dex keeps in `history.state`: the place, and how many steps into the app it is (0: the first). */
export interface NavState {
  dex: 1
  place: Place
  depth: number
}

export const navState = (): NavState | null => {
  const s = window.history.state as NavState | null
  return s && s.dex === 1 && s.place ? s : null
}

export const samePlace = (a: Place | null | undefined, b: Place | null | undefined): boolean =>
  !!a && !!b && JSON.stringify(a) === JSON.stringify(b)

/** The URL for a place: project, thread and tab in the query; panes live in the history entry. */
export function urlFor(place: Place): string {
  const params = new URLSearchParams(window.location.search)
  if (place.project) params.set('project', place.project)
  else params.delete('project')
  if (place.thread) params.set('thread', place.thread)
  else params.delete('thread')
  if (place.view !== 'chat') params.set('view', place.view)
  else params.delete('view')
  return `${window.location.pathname}?${params}${window.location.hash}`
}

/**
 * Records `place` as the current entry: pushed as a new step when the reader
 * navigated (`push`), else replacing the entry they are on. Nothing happens
 * when the entry already holds this place (a back or forward just restored it).
 */
export function record(place: Place, push: boolean): void {
  const now = navState()
  if (samePlace(now?.place, place)) return
  if (push && now) {
    window.history.pushState({ dex: 1, place, depth: now.depth + 1 } satisfies NavState, '', urlFor(place))
  } else {
    window.history.replaceState({ dex: 1, place, depth: now?.depth ?? 0 } satisfies NavState, '', urlFor(place))
  }
}

/** Whether there is a step in dex to go back to (else a pane's ‹ closes it instead of leaving dex). */
export const canGoBack = (): boolean => (navState()?.depth ?? 0) > 0
