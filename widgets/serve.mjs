/**
 * A static file server over the repository root, for widget tests.
 *
 * Port 4319, deliberately outside the 4317/4318 the running dex uses, so tests
 * never collide with a live server or serve stale files from one.
 */
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { extname, join, normalize } from 'node:path'

const ROOT = process.cwd()
const TYPES = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.json': 'application/json', '.css': 'text/css', '.mp4': 'video/mp4',
  '.vtt': 'text/vtt', '.png': 'image/png', '.gif': 'image/gif',
}

createServer(async (request, response) => {
  const path = decodeURIComponent((request.url ?? '/').split('?')[0])
  // Contained in the repository: a test harness is still a web server.
  const target = join(ROOT, normalize(path).replace(/^(\.\.[/\\])+/, ''))
  if (!target.startsWith(ROOT)) {
    response.writeHead(403).end('outside the repository')
    return
  }
  try {
    const body = await readFile(target)
    response.writeHead(200, { 'content-type': TYPES[extname(target)] ?? 'application/octet-stream' })
    response.end(body)
  } catch {
    response.writeHead(404).end('not found')
  }
}).listen(4319, '127.0.0.1', () => console.log('widget test server on :4319'))
