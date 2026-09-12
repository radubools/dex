import type { Pose } from './types'

/**
 * The twenty landmarks and the skeleton between them, exactly as
 * `assets/yoga/AGENTS.md` defines them. Kept here so the viewer and any
 * validation agree on one list.
 */
export const LANDMARKS = [
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
] as const

export const BONES: [string, string][] = [
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

/** A pose file is any JSON with a `landmarks` map of numeric triples. */
export function isPose(value: unknown): value is Pose {
  if (!value || typeof value !== 'object') return false
  const marks = (value as Pose).landmarks
  if (!marks || typeof marks !== 'object') return false
  return Object.values(marks).some(
    (v) => Array.isArray(v) && v.length === 3 && v.every((n) => typeof n === 'number'),
  )
}
