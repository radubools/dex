/** How full a task's context window is, in thousands rounded up: 11,200 is
 * '12k' and 1 is '1k', so a nearly empty window never reads as '0k'. Nothing
 * when there is no usage yet. */
export function formatContextTokens(tokens: number | null | undefined): string | null {
  if (!tokens || tokens <= 0) return null
  return `${Math.ceil(tokens / 1000)}k`
}
