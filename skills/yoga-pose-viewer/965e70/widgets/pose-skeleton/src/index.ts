/**
 * A pose as a 3D skeleton: joints as spheres, bones between them, on a floor
 * grid for orientation. Drag to orbit, right-drag or two fingers to pan,
 * scroll or pinch to zoom.
 *
 * This is the drawing that used to ship inside dex as
 * `web/src/components/PoseViewer3D.tsx`, moved out here. Dex's own viewer knew
 * what a yoga pose was — the landmark list, the bones between them, and a
 * `isPose()` sniff that decided when to use them — which meant one project's
 * subject matter was compiled into everybody's UI. It lives with the project
 * that needs it now, and the viewer decides nothing.
 *
 * Runs in a sandboxed frame with an opaque origin: no network, no session,
 * no stylesheet from the page around it. Everything it needs arrives in `ctx`
 * or is written here.
 */
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

export type Ctx = {
  path: string
  text: string
  theme: string
  fetchAsset: (path: string) => Promise<string>
  fetchAssetUrl: (path: string) => Promise<string>
}

type Pose = {
  pose?: string
  display_name?: string
  landmarks: Record<string, [number, number, number]>
}

/**
 * The twenty landmarks and the skeleton between them, exactly as
 * `assets/yoga/AGENTS.md` defines them.
 */
const LANDMARKS = [
  'TOP_OF_HEAD',
  'TOP_OF_THORACIC_SPINE',
  'TOP_OF_LUMBAR_SPINE',
  'BOTTOM_OF_SACRAL_SPINE',
  'LEFT_SHOULDER', 'RIGHT_SHOULDER',
  'LEFT_ELBOW', 'RIGHT_ELBOW',
  'LEFT_WRIST', 'RIGHT_WRIST',
  'LEFT_CENTRAL_FINGERTIP', 'RIGHT_CENTRAL_FINGERTIP',
  'LEFT_HIP', 'RIGHT_HIP',
  'LEFT_KNEE', 'RIGHT_KNEE',
  'LEFT_ANKLE', 'RIGHT_ANKLE',
  'LEFT_CENTRAL_TOETIP', 'RIGHT_CENTRAL_TOETIP',
]

const BONES: [string, string][] = [
  ['TOP_OF_HEAD', 'TOP_OF_THORACIC_SPINE'],
  ['TOP_OF_THORACIC_SPINE', 'TOP_OF_LUMBAR_SPINE'],
  ['TOP_OF_LUMBAR_SPINE', 'BOTTOM_OF_SACRAL_SPINE'],
  ['TOP_OF_THORACIC_SPINE', 'LEFT_SHOULDER'],
  ['TOP_OF_THORACIC_SPINE', 'RIGHT_SHOULDER'],
  ['LEFT_SHOULDER', 'LEFT_ELBOW'],
  ['LEFT_ELBOW', 'LEFT_WRIST'],
  ['LEFT_WRIST', 'LEFT_CENTRAL_FINGERTIP'],
  ['RIGHT_SHOULDER', 'RIGHT_ELBOW'],
  ['RIGHT_ELBOW', 'RIGHT_WRIST'],
  ['RIGHT_WRIST', 'RIGHT_CENTRAL_FINGERTIP'],
  ['BOTTOM_OF_SACRAL_SPINE', 'LEFT_HIP'],
  ['BOTTOM_OF_SACRAL_SPINE', 'RIGHT_HIP'],
  ['LEFT_HIP', 'LEFT_KNEE'],
  ['LEFT_KNEE', 'LEFT_ANKLE'],
  ['LEFT_ANKLE', 'LEFT_CENTRAL_TOETIP'],
  ['RIGHT_HIP', 'RIGHT_KNEE'],
  ['RIGHT_KNEE', 'RIGHT_ANKLE'],
  ['RIGHT_ANKLE', 'RIGHT_CENTRAL_TOETIP'],
]

/** Warm on the left, cool on the right, so the two sides never read alike. */
const LEFT = 0xf0a35e
const RIGHT = 0x6ec1ff
const MIDLINE = 0xdfe4ee

function sideOf(name: string): number {
  if (name.startsWith('LEFT_')) return LEFT
  if (name.startsWith('RIGHT_')) return RIGHT
  return MIDLINE
}

const CSS = `
.pose { display: grid; grid-template-rows: minmax(0, 1fr) auto; height: 100%;
  min-height: 320px; font: 13px system-ui, sans-serif; color: #e7e9ee; }
.pose-stage { position: relative; min-height: 0; border-radius: 10px; overflow: hidden; }
.pose-stage canvas { display: block; position: absolute; inset: 0; width: 100%; height: 100%; }
.pose-stage .pose-labels { position: absolute; inset: 0; pointer-events: none; }
.pose-controls { display: flex; align-items: center; gap: 12px; padding: 8px 2px 0;
  flex-wrap: wrap; }
.pose-name { font-weight: 600; }
.pose-toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #9aa3b2; }
.pose-toggle input { width: 16px; height: 16px; accent-color: #6ec1ff; }
.pose-hint { font-size: 12px; color: #9aa3b2; }
.pose-missing { font-size: 11px; padding: 2px 8px; border-radius: 999px;
  background: rgba(240,163,94,0.15); color: #f0a35e; }
.pose-error { padding: 12px; color: #ff8a8a; font: 13px ui-monospace, monospace; }
`

export async function mount(host: HTMLElement, ctx: Ctx): Promise<() => void> {
  let pose: Pose
  try {
    pose = JSON.parse(ctx.text) as Pose
  } catch (err) {
    host.innerHTML = `<div class="pose-error">Not readable as JSON: ${String(err)}</div>`
    const style = document.createElement('style')
    style.textContent = CSS
    host.appendChild(style)
    return () => {}
  }
  if (!pose?.landmarks || typeof pose.landmarks !== 'object') {
    host.innerHTML = '<div class="pose-error">No <code>landmarks</code> in this file.</div>'
    return () => {}
  }

  const style = document.createElement('style')
  style.textContent = CSS
  host.appendChild(style)

  const root = document.createElement('div')
  root.className = 'pose'
  const stage = document.createElement('div')
  stage.className = 'pose-stage'
  const controls_bar = document.createElement('div')
  controls_bar.className = 'pose-controls'
  root.append(stage, controls_bar)
  host.appendChild(root)

  const missing = LANDMARKS.filter((name) => !pose.landmarks[name])
  const name = document.createElement('span')
  name.className = 'pose-name'
  name.textContent = pose.display_name || pose.pose || ctx.path.split('/').pop() || 'Pose'

  const toggle = document.createElement('label')
  toggle.className = 'pose-toggle'
  const box = document.createElement('input')
  box.type = 'checkbox'
  toggle.append(box, document.createTextNode('Labels'))

  const hint = document.createElement('span')
  hint.className = 'pose-hint'
  hint.textContent = 'drag · right-drag · scroll'

  controls_bar.append(name, toggle, hint)
  if (missing.length > 0) {
    const chip = document.createElement('span')
    chip.className = 'pose-missing'
    chip.title = missing.join(', ')
    chip.textContent = `${missing.length} missing`
    controls_bar.appendChild(chip)
  }

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(ctx.theme === 'light' ? 0xf4f6fa : 0x0b0d12)

  const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 100)
  const renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  stage.appendChild(renderer.domElement)

  scene.add(new THREE.AmbientLight(0xffffff, 0.75))
  const key = new THREE.DirectionalLight(0xffffff, 1.1)
  key.position.set(2, 4, 3)
  scene.add(key)

  // Y-up, metres, origin on the floor under the hips — the guide's frame.
  scene.add(new THREE.GridHelper(4, 16, 0x2a3242, 0x1a2030))
  scene.add(new THREE.AxesHelper(0.3))

  const points = new Map<string, THREE.Vector3>()
  for (const landmark of LANDMARKS) {
    const value = pose.landmarks[landmark]
    if (value) points.set(landmark, new THREE.Vector3(value[0], value[1], value[2]))
  }

  const joints: { name: string; mesh: THREE.Mesh }[] = []
  const jointGeometry = new THREE.SphereGeometry(0.035, 20, 16)
  for (const [landmark, at] of points) {
    const mesh = new THREE.Mesh(
      jointGeometry,
      new THREE.MeshStandardMaterial({ color: sideOf(landmark), roughness: 0.45 }),
    )
    mesh.position.copy(at)
    mesh.userData.name = landmark
    scene.add(mesh)
    joints.push({ name: landmark, mesh })
  }

  // Bones as cylinders rather than lines: a line has no thickness at depth,
  // which makes a limb pointing at the camera disappear.
  for (const [from, to] of BONES) {
    const a = points.get(from)
    const b = points.get(to)
    if (!a || !b) continue
    const length = a.distanceTo(b)
    if (length < 1e-6) continue
    const bone = new THREE.Mesh(
      new THREE.CylinderGeometry(0.016, 0.016, length, 12),
      new THREE.MeshStandardMaterial({
        color: sideOf(from.startsWith('LEFT_') || from.startsWith('RIGHT_') ? from : to),
        roughness: 0.6,
      }),
    )
    bone.position.copy(a).add(b).multiplyScalar(0.5)
    bone.quaternion.setFromUnitVectors(
      new THREE.Vector3(0, 1, 0),
      b.clone().sub(a).normalize(),
    )
    scene.add(bone)
  }

  // Frame the figure rather than assuming a size: poses range from standing to
  // lying flat.
  const bounds = new THREE.Box3()
  for (const at of points.values()) bounds.expandByPoint(at)
  const centre = bounds.getCenter(new THREE.Vector3())
  const orbit = new OrbitControls(camera, renderer.domElement)
  orbit.enableDamping = true
  orbit.dampingFactor = 0.08
  orbit.target.copy(centre)

  // Framed from the figure's extent and the viewport's aspect. A standing pose
  // in a tall narrow panel needs a very different distance from a lying one in
  // a wide panel, so this is recomputed whenever the panel resizes.
  const size = bounds.getSize(new THREE.Vector3())
  const frameFigure = () => {
    const fov = (camera.fov * Math.PI) / 180
    const forHeight = size.y / 2 / Math.tan(fov / 2)
    const forWidth = Math.max(size.x, size.z) / 2 / (Math.tan(fov / 2) * camera.aspect)
    const distance = Math.max(forHeight, forWidth, 0.5) * 1.5
    const direction = new THREE.Vector3(0.55, 0.28, 1).normalize()
    camera.position.copy(centre).addScaledVector(direction, distance)
    camera.near = Math.max(distance / 100, 0.01)
    camera.far = distance * 20
    camera.updateProjectionMatrix()
    orbit.update()
  }

  // Once someone orbits, stop moving the camera under them.
  let touched = false
  orbit.addEventListener('start', () => { touched = true })

  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  let overName: string | null = null
  const onMove = (event: PointerEvent) => {
    const box_ = renderer.domElement.getBoundingClientRect()
    pointer.x = ((event.clientX - box_.left) / box_.width) * 2 - 1
    pointer.y = -((event.clientY - box_.top) / box_.height) * 2 + 1
    raycaster.setFromCamera(pointer, camera)
    const hit = raycaster.intersectObjects(joints.map((j) => j.mesh))[0]
    const hitName = (hit?.object.userData.name as string) ?? null
    if (hitName !== overName) {
      overName = hitName
      hint.textContent = hitName ? hitName.toLowerCase() : 'drag · right-drag · scroll'
    }
  }
  renderer.domElement.addEventListener('pointermove', onMove)

  // Labels are drawn in an overlay canvas so text stays crisp and unrotated.
  const overlay = document.createElement('canvas')
  overlay.className = 'pose-labels'
  stage.appendChild(overlay)
  const paint = overlay.getContext('2d')

  const resize = () => {
    const w = stage.clientWidth
    const h = stage.clientHeight
    if (!w || !h) return
    renderer.setSize(w, h, false)
    const ratio = Math.min(window.devicePixelRatio, 2)
    overlay.width = w * ratio
    overlay.height = h * ratio
    overlay.style.width = `${w}px`
    overlay.style.height = `${h}px`
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    // Only re-frame until the viewer has been touched; after that the camera
    // is the reader's, not ours.
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
    orbit.update()
    renderer.render(scene, camera)

    if (!paint) return
    const ratio = Math.min(window.devicePixelRatio, 2)
    paint.setTransform(ratio, 0, 0, ratio, 0, 0)
    paint.clearRect(0, 0, overlay.width, overlay.height)
    if (!box.checked && !overName) return
    paint.font = '11px ui-monospace, monospace'
    for (const joint of joints) {
      if (!box.checked && joint.name !== overName) continue
      const at = joint.mesh.position.clone().project(camera)
      if (at.z > 1) continue
      const x = ((at.x + 1) / 2) * stage.clientWidth
      const y = ((1 - at.y) / 2) * stage.clientHeight
      const text = joint.name.toLowerCase()
      const width = paint.measureText(text).width
      paint.fillStyle = 'rgba(11,13,18,0.78)'
      paint.fillRect(x + 8, y - 16, width + 8, 16)
      paint.fillStyle = '#e7e9ee'
      paint.fillText(text, x + 12, y - 4)
    }
  }
  tick()

  return () => {
    alive = false
    observer.disconnect()
    renderer.domElement.removeEventListener('pointermove', onMove)
    orbit.dispose()
    renderer.dispose()
    scene.traverse((object) => {
      if (object instanceof THREE.Mesh) {
        object.geometry.dispose()
        ;(object.material as THREE.Material).dispose()
      }
    })
    host.replaceChildren()
  }
}
