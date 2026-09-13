/**
 * A yoga pose, as an orbitable skeleton.
 *
 * The same figure the built-in viewer drew, rebuilt as a widget to prove the
 * contract against something real. Runs inside a sandboxed frame with an opaque
 * origin: no network, no session, everything it needs arrives in `ctx`.
 */
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

type Point = [number, number, number]

/** Bones drawn between joints. Mirrors what the pose files actually carry. */
const SPINE = [
  'TOP_OF_HEAD',
  'TOP_OF_THORACIC_SPINE',
  'TOP_OF_LUMBAR_SPINE',
  'BOTTOM_OF_SACRAL_SPINE',
]
const LIMB = ['SHOULDER', 'ELBOW', 'WRIST', 'CENTRAL_FINGERTIP']
const LEG = ['HIP', 'KNEE', 'ANKLE', 'CENTRAL_TOETIP']

function bones(): [string, string][] {
  const pairs: [string, string][] = []
  for (let i = 0; i < SPINE.length - 1; i++) pairs.push([SPINE[i], SPINE[i + 1]])
  for (const side of ['LEFT', 'RIGHT'] as const) {
    pairs.push(['TOP_OF_THORACIC_SPINE', `${side}_SHOULDER`])
    pairs.push(['BOTTOM_OF_SACRAL_SPINE', `${side}_HIP`])
    for (let i = 0; i < LIMB.length - 1; i++)
      pairs.push([`${side}_${LIMB[i]}`, `${side}_${LIMB[i + 1]}`])
    for (let i = 0; i < LEG.length - 1; i++)
      pairs.push([`${side}_${LEG[i]}`, `${side}_${LEG[i + 1]}`])
  }
  return pairs
}

export type Ctx = {
  path: string
  text: string
  theme: string
  fetchAsset: (path: string) => Promise<string>
}

export async function mount(el: HTMLElement, ctx: Ctx): Promise<() => void> {
  const pose = JSON.parse(ctx.text) as {
    pose?: string
    display_name?: string
    landmarks: Record<string, Point>
  }
  const landmarks = pose.landmarks ?? {}
  const names = Object.keys(landmarks)
  if (names.length === 0) throw new Error('this file has no `landmarks`')

  const dark = ctx.theme !== 'light'
  const stage = document.createElement('div')
  stage.style.cssText = 'position:relative;width:100%;height:420px'
  const caption = document.createElement('div')
  caption.style.cssText =
    'display:flex;gap:10px;align-items:baseline;padding:6px 2px;font-size:13px;opacity:.75'
  caption.textContent = pose.display_name || pose.pose || ctx.path.split('/').pop() || 'pose'
  const hint = document.createElement('span')
  hint.style.cssText = 'font-size:12px;opacity:.6'
  hint.textContent = 'drag · right-drag · scroll'
  caption.appendChild(hint)
  el.replaceChildren(stage, caption)

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 100)
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
  stage.appendChild(renderer.domElement)

  scene.add(new THREE.AmbientLight(0xffffff, 0.8))
  const key = new THREE.DirectionalLight(0xffffff, 0.7)
  key.position.set(2, 4, 3)
  scene.add(key)
  const grid = new THREE.GridHelper(4, 16, dark ? 0x2a2f3a : 0xc9ced8, dark ? 0x1c212a : 0xe2e6ec)
  scene.add(grid)

  const jointColour = dark ? 0x7fd8ff : 0x0b6e99
  const boneColour = dark ? 0x9aa4b2 : 0x53607a
  const geometry = new THREE.SphereGeometry(0.028, 20, 16)
  const material = new THREE.MeshStandardMaterial({ color: jointColour })
  const points = new Map<string, THREE.Vector3>()

  for (const [name, at] of Object.entries(landmarks)) {
    if (!Array.isArray(at) || at.length !== 3 || at.some((n) => typeof n !== 'number')) continue
    const vector = new THREE.Vector3(at[0], at[1], at[2])
    points.set(name, vector)
    const mesh = new THREE.Mesh(geometry, material)
    mesh.position.copy(vector)
    scene.add(mesh)
  }

  const boneMaterial = new THREE.LineBasicMaterial({ color: boneColour })
  for (const [a, b] of bones()) {
    const from = points.get(a)
    const to = points.get(b)
    if (!from || !to) continue
    scene.add(
      new THREE.Line(new THREE.BufferGeometry().setFromPoints([from, to]), boneMaterial),
    )
  }

  const box = new THREE.Box3()
  for (const at of points.values()) box.expandByPoint(at)
  const centre = box.getCenter(new THREE.Vector3())
  const size = box.getSize(new THREE.Vector3())

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.target.copy(centre)

  // Only reframe until the reader takes over, the same rule the built-in
  // viewer learned: a resize must not undo somebody's camera.
  let touched = false
  controls.addEventListener('start', () => {
    touched = true
  })

  const frameFigure = () => {
    const fov = (camera.fov * Math.PI) / 180
    const forHeight = size.y / 2 / Math.tan(fov / 2)
    const forWidth = Math.max(size.x, size.z) / 2 / (Math.tan(fov / 2) * camera.aspect)
    const distance = Math.max(forHeight, forWidth, 0.5) * 1.5
    camera.position.copy(centre).addScaledVector(new THREE.Vector3(0.55, 0.28, 1).normalize(), distance)
    camera.near = Math.max(distance / 100, 0.01)
    camera.far = distance * 20
    camera.updateProjectionMatrix()
    controls.update()
  }

  const resize = () => {
    const w = stage.clientWidth || 600
    const h = stage.clientHeight || 420
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    if (!touched) frameFigure()
  }
  const observer = new ResizeObserver(resize)
  observer.observe(stage)
  resize()
  frameFigure()

  let alive = true
  const tick = () => {
    if (!alive) return
    requestAnimationFrame(tick)
    controls.update()
    renderer.render(scene, camera)
  }
  tick()

  return () => {
    alive = false
    observer.disconnect()
    controls.dispose()
    renderer.dispose()
    geometry.dispose()
    material.dispose()
  }
}
