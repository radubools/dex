/**
 * A game asset (.glb) on a tile grid, seen the way the game sees it.
 *
 * The camera starts isometric (yaw 45°, pitch 30°) and orbits like an MMO
 * camera: drag to rotate, right-drag / two fingers to pan, scroll / pinch to
 * zoom, never below the ground. Orthographic is a toggle, because the game's
 * default view is a flat iso projection and perspective hides proportion.
 *
 * It knows the `game-mesh` conventions and nothing else of the game:
 *   - Y up, 1 unit = 1 tile, origin at the centre of the footprint on y = 0
 *     → the floor grid is 1-unit squares, offset by half a tile on an axis
 *       whose footprint is odd so the model sits on whole tiles.
 *   - node animation clips → a clip picker on an AnimationMixer.
 *   - `stage_*` nodes on landmarks → a construction slider, bottom-up in name
 *     order (the order `preview.stage_strip` uses), with `scaffold` shown
 *     while the building is unfinished.
 *   - a material named `team` → tinted with the game's TEAM_COLORS.
 *   - emissive textures for lit windows → a day/night toggle that raises
 *     emissive strength and dims the sun.
 *
 * Runs in a sandboxed frame with an opaque origin: the file's bytes come only
 * through `ctx.fetchAssetUrl`, and the package's `manifest.json` (if any)
 * through `ctx.fetchAsset`.
 */
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader, type GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js'

export type Ctx = {
  path: string
  text: string
  theme: string
  fetchAsset: (path: string) => Promise<string>
  fetchAssetUrl: (path: string) => Promise<string>
}

/** `TEAM_COLORS` from ~/games/many-builders/src/config.js, in the game's order. */
export const TEAM_COLORS = [
  '#ff8c1a', '#3fa7ff', '#ff4d6d', '#3ddc84', '#c77dff',
  '#ffd23f', '#00d1c1', '#ff7ab6', '#9be15d', '#e8e8e8',
]

const YAW = 45
const PITCH = 30
const FOV = 35

type Palette = { bg: number; fg: string; muted: string; panel: string; line: string;
  accent: string; grid: number; gridMajor: number; input: string }

const PALETTES: Record<'light' | 'dark', Palette> = {
  dark: { bg: 0x10131a, fg: '#e7e9ee', muted: '#9aa3b2', panel: 'rgba(16,19,26,0.82)',
    line: 'rgba(255,255,255,0.12)', accent: '#6ec1ff', grid: 0x252c3a, gridMajor: 0x3a4458,
    input: '#1a1f29' },
  light: { bg: 0xeef1f5, fg: '#14161a', muted: '#5b6472', panel: 'rgba(255,255,255,0.86)',
    line: 'rgba(0,0,0,0.12)', accent: '#1f6fd1', grid: 0xc9d0da, gridMajor: 0xa7b1bf,
    input: '#ffffff' },
}

const NIGHT_BG = 0x0a0e1c

const css = (p: Palette) => `
.glbv { font: 13px system-ui, -apple-system, sans-serif; color: ${p.fg}; }
.glbv * { box-sizing: border-box; }
.glbv-stage { position: relative; height: 480px; border-radius: 10px; overflow: hidden;
  border: 1px solid ${p.line}; touch-action: none; }
@media (max-width: 560px) { .glbv-stage { height: 360px; } }
.glbv-stage canvas { display: block; position: absolute; inset: 0; width: 100%; height: 100%; }
.glbv-info { position: absolute; top: 8px; left: 8px; max-width: min(260px, 70%);
  background: ${p.panel}; border: 1px solid ${p.line}; border-radius: 8px;
  padding: 6px 10px; font-size: 12px; backdrop-filter: blur(4px); }
.glbv-info summary { cursor: pointer; font-weight: 600; list-style: none; }
.glbv-info summary::-webkit-details-marker { display: none; }
.glbv-info summary::before { content: '▸ '; color: ${p.muted}; }
.glbv-info[open] summary::before { content: '▾ '; }
.glbv-info dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; margin: 6px 0 2px; }
.glbv-info dt { color: ${p.muted}; }
.glbv-info dd { margin: 0; font-variant-numeric: tabular-nums; word-break: break-word; }
.glbv-hint { position: absolute; right: 8px; bottom: 6px; font-size: 11px; color: ${p.muted};
  pointer-events: none; text-shadow: 0 0 4px ${p.panel}; }
.glbv-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; padding: 8px 2px 2px; }
.glbv-group { display: flex; align-items: center; gap: 6px; }
.glbv-group > .glbv-label { color: ${p.muted}; font-size: 12px; }
.glbv button, .glbv select { font: inherit; font-size: 12px; color: ${p.fg}; background: ${p.input};
  border: 1px solid ${p.line}; border-radius: 6px; padding: 4px 8px; cursor: pointer; min-height: 28px; }
.glbv button[aria-pressed="true"] { border-color: ${p.accent}; color: ${p.accent}; }
.glbv input[type=range] { accent-color: ${p.accent}; width: 110px; }
.glbv input[type=checkbox] { accent-color: ${p.accent}; width: 15px; height: 15px; margin: 0; }
.glbv-num { font-variant-numeric: tabular-nums; font-size: 12px; min-width: 3.2em; color: ${p.muted}; }
.glbv-swatches { display: flex; gap: 4px; flex-wrap: wrap; }
.glbv .glbv-swatch { width: 22px; height: 22px; min-height: 22px; padding: 0; border-radius: 50%;
  border: 2px solid transparent; box-shadow: inset 0 0 0 1px rgba(0,0,0,0.25); }
.glbv .glbv-swatch[aria-pressed="true"] { border-color: ${p.fg}; }
.glbv-error { padding: 12px; color: #ff6b6b; font: 12px ui-monospace, monospace; white-space: pre-wrap; }
.glbv-loading { padding: 12px; color: ${p.muted}; }
`

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, props: Partial<HTMLElementTagNameMap[K]> & { className?: string } = {}, ...kids: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  Object.assign(node, props)
  node.append(...kids)
  return node
}

function group(label: string, ...kids: (Node | string)[]): HTMLDivElement {
  return el('div', { className: 'glbv-group' }, el('span', { className: 'glbv-label', textContent: label }), ...kids)
}

/** Every material on a mesh, as an array. */
function materialsOf(mesh: THREE.Mesh): THREE.Material[] {
  return Array.isArray(mesh.material) ? mesh.material : [mesh.material]
}

export async function mount(host: HTMLElement, ctx: Ctx): Promise<() => void> {
  const theme: 'light' | 'dark' = ctx.theme === 'light' ? 'light' : 'dark'
  const pal = PALETTES[theme]
  const style = el('style', { textContent: css(pal) })
  const root = el('div', { className: 'glbv' })
  host.append(style, root)
  root.append(el('div', { className: 'glbv-loading', textContent: 'Loading model…' }))

  const name = ctx.path.split('/').pop() ?? ''
  let objectUrl = ''
  let gltf: GLTF
  let bytes = 0
  try {
    objectUrl = await ctx.fetchAssetUrl(name)
    const buffer = await (await fetch(objectUrl)).arrayBuffer()
    bytes = buffer.byteLength
    gltf = await new GLTFLoader().parseAsync(buffer, '')
  } catch (err) {
    if (objectUrl) URL.revokeObjectURL(objectUrl)
    root.replaceChildren(el('div', { className: 'glbv-error', textContent: `Could not load ${name}: ${String(err)}` }))
    return () => host.replaceChildren()
  }

  // The package manifest is optional context: its kind and footprint.
  let manifest: Record<string, unknown> | null = null
  try {
    manifest = JSON.parse(await ctx.fetchAsset('manifest.json'))
  } catch {
    manifest = null
  }

  // ------------------------------------------------------------------ scene
  const model = gltf.scene
  const scene = new THREE.Scene()
  scene.add(model)

  let triangles = 0
  const meshes: THREE.Mesh[] = []
  const materials = new Set<THREE.Material>()
  model.traverse((obj) => {
    const mesh = obj as THREE.Mesh
    if (!mesh.isMesh) return
    mesh.castShadow = true
    mesh.receiveShadow = true
    meshes.push(mesh)
    const g = mesh.geometry
    triangles += (g.index ? g.index.count : g.attributes.position.count) / 3
    for (const m of materialsOf(mesh)) materials.add(m)
  })

  const json = gltf.parser.json as {
    nodes?: { name?: string }[]; materials?: { name?: string }[]
  }
  const nodeCount = json.nodes?.length ?? 0

  // Team-tinted materials keep their authored colour so the tint is a multiply.
  const teamMaterials = [...materials].filter(
    (m) => /^team(\b|_|$)/i.test(m.name) && 'color' in m,
  ) as THREE.MeshStandardMaterial[]
  const teamBase = new Map(teamMaterials.map((m) => [m, m.color.clone()]))

  // Emissive materials: their authored intensity is the night value's base.
  const emissive = [...materials].filter(
    (m) => (m as THREE.MeshStandardMaterial).emissive &&
      ((m as THREE.MeshStandardMaterial).emissiveMap ||
        (m as THREE.MeshStandardMaterial).emissive.getHex() !== 0),
  ) as THREE.MeshStandardMaterial[]
  const emissiveBase = new Map(emissive.map((m) => [m, m.emissiveIntensity]))

  // Construction stages, bottom-up in name order.
  const stages: THREE.Object3D[] = []
  let scaffold: THREE.Object3D | undefined
  model.traverse((obj) => {
    if (obj.name.startsWith('stage_')) stages.push(obj)
    else if (obj.name === 'scaffold' && !scaffold) scaffold = obj
  })
  stages.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))

  // Bounds with everything visible, in the rest pose.
  model.updateMatrixWorld(true)
  const bounds = new THREE.Box3().setFromObject(model)
  if (bounds.isEmpty()) bounds.set(new THREE.Vector3(-0.5, 0, -0.5), new THREE.Vector3(0.5, 1, 0.5))
  const size = bounds.getSize(new THREE.Vector3())
  const centre = bounds.getCenter(new THREE.Vector3())
  const radius = Math.max(bounds.getBoundingSphere(new THREE.Sphere()).radius, 0.25)

  // Footprint in tiles: from the manifest when it says, else the bounds.
  const fp = Array.isArray(manifest?.footprint) ? (manifest!.footprint as number[]) : null
  const footX = Math.max(1, Math.round(fp?.[0] ?? size.x))
  const footZ = Math.max(1, Math.round(fp?.[1] ?? size.z))

  // Tile grid: 1-unit squares, aligned so the footprint covers whole tiles.
  const gridSize = Math.max(8, 2 * Math.ceil(Math.max(Math.abs(bounds.min.x), Math.abs(bounds.max.x),
    Math.abs(bounds.min.z), Math.abs(bounds.max.z)) + 3))
  const grid = new THREE.GridHelper(gridSize, gridSize, pal.gridMajor, pal.grid)
  const gridMaterial = grid.material as THREE.LineBasicMaterial
  gridMaterial.transparent = true
  grid.position.set(footX % 2 ? 0.5 : 0, 0.001, footZ % 2 ? 0.5 : 0)
  scene.add(grid)
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(gridSize * 2, gridSize * 2),
    new THREE.ShadowMaterial({ opacity: theme === 'light' ? 0.22 : 0.4 }),
  )
  ground.rotation.x = -Math.PI / 2
  ground.receiveShadow = true
  scene.add(ground)

  // Lights: sky/ground hemisphere and a shadow-casting sun from the south-east.
  const hemi = new THREE.HemisphereLight(0xdfeaff, 0x5a4a3a, 1.4)
  scene.add(hemi)
  const sun = new THREE.DirectionalLight(0xfff1dc, 2.6)
  const sunDir = new THREE.Vector3(0.55, 1, 0.35).normalize()
  sun.position.copy(centre).addScaledVector(sunDir, radius * 4)
  sun.target.position.copy(centre)
  sun.castShadow = true
  sun.shadow.mapSize.set(2048, 2048)
  const sc = sun.shadow.camera
  const reach = Math.max(radius * 1.6, gridSize / 2)
  sc.left = -reach; sc.right = reach; sc.top = reach; sc.bottom = -reach
  sc.near = 0.01; sc.far = radius * 10
  sun.shadow.bias = -0.0005
  sun.shadow.normalBias = 0.02
  scene.add(sun, sun.target)

  const renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.outputColorSpace = THREE.SRGBColorSpace

  // ---------------------------------------------------------------- layout
  const stage = el('div', { className: 'glbv-stage' })
  stage.appendChild(renderer.domElement)
  const hint = el('div', { className: 'glbv-hint', textContent: 'drag rotate · right-drag pan · scroll zoom' })
  stage.appendChild(hint)

  const info = el('details', { className: 'glbv-info', open: true })
  const fmt = (n: number) => (Math.round(n * 100) / 100).toString()
  const rows: [string, string][] = [
    ['file', `${name} · ${(bytes / 1024).toFixed(0)} KB`],
  ]
  if (manifest?.kind) rows.push(['kind', String(manifest.kind)])
  rows.push(
    ['triangles', Math.round(triangles).toLocaleString()],
    ['nodes', String(nodeCount)],
    ['materials', `${json.materials?.length ?? materials.size}` +
      (json.materials?.length ? ` · ${json.materials.map((m) => m.name ?? '?').join(', ')}` : '')],
    ['clips', gltf.animations.length
      ? gltf.animations.map((c) => `${c.name} ${c.duration.toFixed(1)}s`).join(', ') : 'none'],
    ['bounds', `${fmt(size.x)} × ${fmt(size.z)} tiles, ${fmt(size.y)} high`],
  )
  if (fp) rows.push(['footprint', `${fp[0]} × ${fp[1]} tiles (manifest)`])
  if (stages.length) rows.push(['stages', String(stages.length) + (scaffold ? ' + scaffold' : '')])
  const dl = el('dl')
  for (const [k, v] of rows) dl.append(el('dt', { textContent: k }), el('dd', { textContent: v }))
  info.append(el('summary', { textContent: (manifest?.name as string) || name.replace(/\.glb$/i, '') }), dl)
  stage.appendChild(info)

  const bar = el('div', { className: 'glbv-bar' })
  root.replaceChildren(stage, bar)

  // ---------------------------------------------------------------- camera
  const perspective = new THREE.PerspectiveCamera(FOV, 1, 0.01, 1000)
  const ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 1000)
  let camera: THREE.PerspectiveCamera | THREE.OrthographicCamera = ortho
  let orthoHalf = 1 // ortho frustum half-height at zoom 1
  let aspect = 1

  const makeControls = (cam: THREE.Camera) => {
    const c = new OrbitControls(cam, renderer.domElement)
    c.enableDamping = true
    c.dampingFactor = 0.08
    c.maxPolarAngle = Math.PI / 2 - 0.03 // never below the ground
    c.minDistance = radius * 0.3
    c.maxDistance = radius * 30
    c.minZoom = 0.15
    c.maxZoom = 12
    c.screenSpacePanning = false // pan across the ground, like an MMO camera
    c.addEventListener('start', () => { touched = true })
    return c
  }
  let touched = false
  let controls = makeControls(camera)

  const applyFrustum = () => {
    perspective.aspect = aspect
    perspective.updateProjectionMatrix()
    ortho.left = -orthoHalf * aspect
    ortho.right = orthoHalf * aspect
    ortho.top = orthoHalf
    ortho.bottom = -orthoHalf
    ortho.updateProjectionMatrix()
  }

  /** The iso view: yaw 45°, pitch 30°, everything in frame. */
  const frame = () => {
    const yaw = THREE.MathUtils.degToRad(YAW)
    const pitch = THREE.MathUtils.degToRad(PITCH)
    const dir = new THREE.Vector3(Math.cos(pitch) * Math.sin(yaw), Math.sin(pitch), Math.cos(pitch) * Math.cos(yaw))
    const fit = radius * 1.08
    const vfov = THREE.MathUtils.degToRad(FOV)
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * aspect)
    const distance = fit / Math.sin(Math.min(vfov, hfov) / 2)
    orthoHalf = fit / Math.min(1, aspect)
    applyFrustum()
    const dist = camera === ortho ? Math.max(distance, radius * 4) : distance
    controls.target.copy(centre)
    camera.position.copy(centre).addScaledVector(dir, dist)
    camera.near = Math.max(dist / 200, 0.005)
    camera.far = dist + radius * 40
    camera.zoom = 1
    camera.updateProjectionMatrix()
    controls.update()
  }

  /** Swap projection, keeping the view direction and apparent size. */
  const setProjection = (orthographic: boolean) => {
    if ((camera === ortho) === orthographic) return
    const from = camera
    const to = orthographic ? ortho : perspective
    const target = controls.target.clone()
    const offset = from.position.clone().sub(target)
    const vtan = Math.tan(THREE.MathUtils.degToRad(FOV) / 2)
    if (orthographic) {
      orthoHalf = offset.length() * vtan
      ortho.zoom = 1
      offset.setLength(Math.max(offset.length(), radius * 4))
    } else {
      const visible = orthoHalf / ortho.zoom
      offset.setLength(Math.min(Math.max(visible / vtan, radius * 0.3), radius * 30))
    }
    to.position.copy(target).add(offset)
    to.near = Math.max(offset.length() / 200, 0.005)
    to.far = offset.length() + radius * 40
    camera = to
    applyFrustum()
    controls.dispose()
    controls = makeControls(camera)
    controls.target.copy(target)
    controls.update()
  }

  // ------------------------------------------------------------- animation
  const mixer = new THREE.AnimationMixer(model)
  let action: THREE.AnimationAction | null = null
  let playing = true
  let speed = 1
  let loop = true

  const clipSelect = el('select', { title: 'Animation clip' })
  clipSelect.append(el('option', { value: '', textContent: 'rest pose' }))
  for (const clip of gltf.animations) clipSelect.append(el('option', { value: clip.name, textContent: clip.name }))
  const playBtn = el('button', { type: 'button', textContent: 'Pause', title: 'Play / pause' })
  const speedInput = el('input', { type: 'range', min: '0.1', max: '3', step: '0.1', value: '1', title: 'Speed' })
  const speedOut = el('span', { className: 'glbv-num', textContent: '1.0×' })
  const loopBox = el('input', { type: 'checkbox', checked: true, title: 'Loop' })

  const useClip = (clipName: string) => {
    mixer.stopAllAction()
    action = null
    const clip = gltf.animations.find((c) => c.name === clipName)
    if (clip) {
      action = mixer.clipAction(clip)
      action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity)
      action.clampWhenFinished = true
      action.paused = !playing
      action.play()
    }
    playBtn.disabled = !clip
  }
  clipSelect.addEventListener('change', () => useClip(clipSelect.value))
  playBtn.addEventListener('click', () => {
    if (!action) return
    // A one-shot clip that has finished restarts on play.
    if (!loop && !action.isRunning() && !action.paused) action.reset().play()
    playing = !playing
    action.paused = !playing
    playBtn.textContent = playing ? 'Pause' : 'Play'
  })
  speedInput.addEventListener('input', () => {
    speed = Number(speedInput.value)
    speedOut.textContent = `${speed.toFixed(1)}×`
  })
  loopBox.addEventListener('change', () => {
    loop = loopBox.checked
    if (action) {
      action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity)
      if (loop) action.reset().play()
    }
  })
  mixer.addEventListener('finished', () => {
    playing = false
    playBtn.textContent = 'Play'
  })

  if (gltf.animations.length) {
    const first = gltf.animations.find((c) => c.name === 'idle') ?? gltf.animations[0]
    clipSelect.value = first.name
    useClip(first.name)
    bar.append(group('Clip', clipSelect, playBtn),
      group('Speed', speedInput, speedOut),
      el('label', { className: 'glbv-group' }, loopBox, el('span', { className: 'glbv-label', textContent: 'Loop' })))
  }

  // ---------------------------------------------------------- construction
  if (stages.length) {
    const slider = el('input', { type: 'range', min: '0', max: String(stages.length), step: '1',
      value: String(stages.length), title: 'Construction progress' })
    const out = el('span', { className: 'glbv-num' })
    const showStages = (built: number) => {
      stages.forEach((s, i) => { s.visible = i < built })
      if (scaffold) scaffold.visible = built > 0 && built < stages.length
      out.textContent = `${built}/${stages.length}`
    }
    slider.addEventListener('input', () => showStages(Number(slider.value)))
    showStages(stages.length)
    bar.append(group('Build', slider, out))
    slider.dataset.role = 'stages'
  }

  // ---------------------------------------------------------- team colours
  if (teamMaterials.length) {
    const box = el('div', { className: 'glbv-swatches' })
    const buttons = TEAM_COLORS.map((hex, i) => {
      const b = el('button', { type: 'button', className: 'glbv-swatch', title: `Team ${i + 1} ${hex}` })
      b.style.background = hex
      b.addEventListener('click', () => setTeam(i))
      return b
    })
    const setTeam = (i: number) => {
      const tint = new THREE.Color(TEAM_COLORS[i])
      for (const m of teamMaterials) m.color.copy(teamBase.get(m)!).multiply(tint)
      buttons.forEach((b, j) => b.setAttribute('aria-pressed', String(i === j)))
    }
    box.append(...buttons)
    setTeam(0)
    bar.append(group('Team', box))
  }

  // ------------------------------------------------------- view toggles
  const isoBtn = el('button', { type: 'button', textContent: 'Ortho', title: 'Orthographic / perspective' })
  isoBtn.setAttribute('aria-pressed', 'true')
  isoBtn.addEventListener('click', () => {
    const next = camera !== ortho
    setProjection(next)
    isoBtn.setAttribute('aria-pressed', String(next))
  })
  const resetBtn = el('button', { type: 'button', textContent: 'Iso view', title: 'Reset to the isometric view' })
  resetBtn.addEventListener('click', () => { touched = false; frame() })

  let night = false
  const nightBtn = el('button', { type: 'button', textContent: 'Night', title: 'Day / night' })
  nightBtn.setAttribute('aria-pressed', 'false')
  const applyTime = () => {
    scene.background = new THREE.Color(night ? NIGHT_BG : pal.bg)
    hemi.color.set(night ? 0x34406a : 0xdfeaff)
    hemi.groundColor.set(night ? 0x0c0c14 : 0x5a4a3a)
    hemi.intensity = night ? 0.9 : 1.4
    sun.color.set(night ? 0x9fb4ff : 0xfff1dc)
    sun.intensity = night ? 0.7 : 2.6
    for (const m of emissive) m.emissiveIntensity = (emissiveBase.get(m) ?? 1) * (night ? 1.5 : 0.15)
    gridMaterial.opacity = night ? 0.3 : 1
    nightBtn.setAttribute('aria-pressed', String(night))
  }
  nightBtn.addEventListener('click', () => { night = !night; applyTime() })
  applyTime()

  const gridBox = el('input', { type: 'checkbox', checked: true, title: 'Tile grid' })
  gridBox.addEventListener('change', () => { grid.visible = gridBox.checked })
  bar.append(group('View', isoBtn, resetBtn, nightBtn),
    el('label', { className: 'glbv-group' }, gridBox, el('span', { className: 'glbv-label', textContent: 'Grid' })))

  // ------------------------------------------------------- hover a part
  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  const defaultHint = hint.textContent
  const nodeNames = new Set((json.nodes ?? []).map((n) => THREE.PropertyBinding.sanitizeNodeName(n.name ?? '')))
  const onMove = (event: PointerEvent) => {
    if (event.buttons) return
    const r = renderer.domElement.getBoundingClientRect()
    pointer.set(((event.clientX - r.left) / r.width) * 2 - 1, -((event.clientY - r.top) / r.height) * 2 + 1)
    raycaster.setFromCamera(pointer, camera)
    const hit = raycaster.intersectObjects(meshes.filter((m) => {
      for (let o: THREE.Object3D | null = m; o; o = o.parent) if (!o.visible) return false
      return true
    }), false)[0]
    let node: THREE.Object3D | null = hit?.object ?? null
    // A multi-material node loads as a group of primitives; report the node.
    while (node && !nodeNames.has(node.name)) node = node.parent
    hint.textContent = node ? node.name : defaultHint
  }
  renderer.domElement.addEventListener('pointermove', onMove)

  // ------------------------------------------------------------- render loop
  const resize = () => {
    const w = stage.clientWidth
    const h = stage.clientHeight
    if (!w || !h) return
    renderer.setSize(w, h, false)
    aspect = w / h
    if (touched) applyFrustum()
    else frame()
  }
  const observer = new ResizeObserver(resize)
  observer.observe(stage)
  resize()
  frame()
  // On a phone the stats panel would cover the model; start it folded.
  if (stage.clientWidth < 560) info.open = false

  const clock = new THREE.Clock()
  let raf = 0
  let alive = true
  const tick = () => {
    if (!alive) return
    raf = requestAnimationFrame(tick)
    mixer.update(clock.getDelta() * speed)
    controls.update()
    renderer.render(scene, camera)
  }
  tick()

  // For tests and debugging: what was found in the file.
  ;(root as HTMLElement & { __glb?: unknown }).__glb = {
    triangles, nodes: nodeCount, stages: stages.map((s) => s.name), team: teamMaterials.length,
    clips: gltf.animations.map((c) => c.name), emissive: emissive.length,
    camera: () => (camera === ortho ? 'orthographic' : 'perspective'),
    night: () => night,
  }

  return () => {
    alive = false
    cancelAnimationFrame(raf)
    observer.disconnect()
    renderer.domElement.removeEventListener('pointermove', onMove)
    mixer.stopAllAction()
    mixer.uncacheRoot(model)
    controls.dispose()
    const textures = new Set<THREE.Texture>()
    scene.traverse((obj) => {
      const mesh = obj as THREE.Mesh
      if (mesh.isMesh || (obj as THREE.LineSegments).isLineSegments) {
        mesh.geometry?.dispose()
        for (const m of materialsOf(mesh)) {
          for (const v of Object.values(m)) if (v instanceof THREE.Texture) textures.add(v)
          m.dispose()
        }
      }
    })
    for (const t of textures) t.dispose()
    sun.shadow.map?.dispose()
    renderer.dispose()
    renderer.forceContextLoss()
    URL.revokeObjectURL(objectUrl)
    host.replaceChildren()
  }
}
