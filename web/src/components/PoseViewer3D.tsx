import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { Pose } from '../types'
import { BONES, LANDMARKS } from '../pose'

/** Warm on the left, cool on the right, so the two sides never read alike. */
const LEFT = 0xf0a35e
const RIGHT = 0x6ec1ff
const MIDLINE = 0xdfe4ee

function sideOf(name: string): number {
  if (name.startsWith('LEFT_')) return LEFT
  if (name.startsWith('RIGHT_')) return RIGHT
  return MIDLINE
}

/**
 * A pose as a 3D skeleton: joints as spheres, bones between them, on a floor
 * grid for orientation. Drag to orbit, right-drag or two fingers to pan, scroll
 * or pinch to zoom.
 *
 * The scene is built once per pose and driven by an imperative Three.js loop,
 * so React re-renders never touch it.
 */
export function PoseViewer3D({ pose }: { pose: Pose }) {
  const host = useRef<HTMLDivElement>(null)
  const [labels, setLabels] = useState(false)
  const [hovered, setHovered] = useState<string | null>(null)
  const showLabels = useRef(labels)

  // Rebuild on the figure, not on the object. A caller that re-parses the same
  // file hands us an equal-but-new `pose`, and tearing the scene down for that
  // threw the reader's camera away mid-orbit. The effect below reads only
  // `pose.landmarks`, which is exactly what this covers, so an identity change
  // with the same landmarks is genuinely nothing to redo. Twenty points, so
  // stringifying it is cheaper than the rebuild it prevents.
  const figure = useMemo(() => JSON.stringify(pose.landmarks), [pose])
  showLabels.current = labels

  useEffect(() => {
    const mount = host.current
    if (!mount) return

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x0b0d12)

    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 100)
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    mount.appendChild(renderer.domElement)

    scene.add(new THREE.AmbientLight(0xffffff, 0.75))
    const key = new THREE.DirectionalLight(0xffffff, 1.1)
    key.position.set(2, 4, 3)
    scene.add(key)

    // Y-up, metres, origin on the floor under the hips — the guide's frame.
    const grid = new THREE.GridHelper(4, 16, 0x2a3242, 0x1a2030)
    scene.add(grid)
    scene.add(new THREE.AxesHelper(0.3))

    const points = new Map<string, THREE.Vector3>()
    for (const name of LANDMARKS) {
      const value = pose.landmarks[name]
      if (value) points.set(name, new THREE.Vector3(value[0], value[1], value[2]))
    }

    const joints: { name: string; mesh: THREE.Mesh }[] = []
    const jointGeometry = new THREE.SphereGeometry(0.035, 20, 16)
    for (const [name, at] of points) {
      const mesh = new THREE.Mesh(
        jointGeometry,
        new THREE.MeshStandardMaterial({ color: sideOf(name), roughness: 0.45 }),
      )
      mesh.position.copy(at)
      mesh.userData.name = name
      scene.add(mesh)
      joints.push({ name, mesh })
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

    // Frame the figure rather than assuming a size: poses range from standing
    // to lying flat.
    const bounds = new THREE.Box3()
    for (const at of points.values()) bounds.expandByPoint(at)
    const centre = bounds.getCenter(new THREE.Vector3())
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.target.copy(centre)

    // Frame from the figure's extent and the viewport's aspect. A standing
    // pose in a tall narrow panel needs a very different distance from a lying
    // one in a wide panel, so this is recomputed whenever the panel resizes.
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
      controls.update()
    }

    // Once someone orbits, stop moving the camera under them.
    let touched = false
    controls.addEventListener('start', () => { touched = true })

    const raycaster = new THREE.Raycaster()
    const pointer = new THREE.Vector2()
    let overName: string | null = null
    const onMove = (event: PointerEvent) => {
      const box = renderer.domElement.getBoundingClientRect()
      pointer.x = ((event.clientX - box.left) / box.width) * 2 - 1
      pointer.y = -((event.clientY - box.top) / box.height) * 2 + 1
      raycaster.setFromCamera(pointer, camera)
      const hit = raycaster.intersectObjects(joints.map((j) => j.mesh))[0]
      const name = (hit?.object.userData.name as string) ?? null
      if (name !== overName) {
        overName = name
        setHovered(name)
      }
    }
    renderer.domElement.addEventListener('pointermove', onMove)

    // Labels are drawn in an overlay canvas so text stays crisp and unrotated.
    const overlay = document.createElement('canvas')
    overlay.className = 'pose-labels'
    mount.appendChild(overlay)
    const ctx = overlay.getContext('2d')

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = mount
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
    observer.observe(mount)
    resize()
    frameFigure()

    let alive = true
    const frame = () => {
      if (!alive) return
      requestAnimationFrame(frame)
      controls.update()
      renderer.render(scene, camera)

      if (!ctx) return
      const ratio = Math.min(window.devicePixelRatio, 2)
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
      ctx.clearRect(0, 0, overlay.width, overlay.height)
      if (!showLabels.current && !overName) return
      ctx.font = '11px ui-monospace, monospace'
      ctx.fillStyle = '#e7e9ee'
      for (const { name, mesh } of joints) {
        if (!showLabels.current && name !== overName) continue
        const at = mesh.position.clone().project(camera)
        if (at.z > 1) continue
        const x = ((at.x + 1) / 2) * mount.clientWidth
        const y = ((1 - at.y) / 2) * mount.clientHeight
        const text = name.toLowerCase()
        const width = ctx.measureText(text).width
        ctx.fillStyle = 'rgba(11,13,18,0.78)'
        ctx.fillRect(x + 8, y - 16, width + 8, 16)
        ctx.fillStyle = '#e7e9ee'
        ctx.fillText(text, x + 12, y - 4)
      }
    }
    frame()

    return () => {
      alive = false
      observer.disconnect()
      renderer.domElement.removeEventListener('pointermove', onMove)
      controls.dispose()
      renderer.dispose()
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose()
          ;(object.material as THREE.Material).dispose()
        }
      })
      mount.replaceChildren()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [figure])

  const missing = LANDMARKS.filter((name) => !pose.landmarks[name])

  return (
    <div className="pose">
      <div className="pose-stage" ref={host} />
      <div className="pose-controls">
        <span className="pose-name">{pose.display_name || pose.pose}</span>
        <label className="pose-toggle">
          <input type="checkbox" checked={labels} onChange={(e) => setLabels(e.target.checked)} />
          Labels
        </label>
        <span className="muted small">{hovered?.toLowerCase() ?? 'drag · right-drag · scroll'}</span>
        {missing.length > 0 && (
          <span className="chip warn" title={missing.join(', ')}>
            {missing.length} missing
          </span>
        )}
      </div>
    </div>
  )
}
