## `rig`

Place a limb by saying where it should reach, not by typing coordinates.

- `add(a, b)` — a + b.
- `sub(a, b)` — a - b.
- `scale(a, k)` — a * k.
- `dot(a, b)` — The dot product of a and b.
- `cross(a, b)` — The cross product of a and b, right-handed like the pose files.
- `norm(a)` — The length of a.
- `unit(a)` — a scaled to length 1. Raises on the zero vector.
- `lerp(a, b, t)` — The point `t` of the way from a to b, so `t=0` is a and `t=1` is b.
- `along(root, target, length)` — The point `length` meters from `root` toward `target`.
- `lean(degrees, toward=(1.0, 0.0, 0.0))` — A unit vector `degrees` off straight up, tipped `toward` a direction.
- `rotate(v, axis, degrees)` — `v` turned `degrees` about `axis`, right-handed, by Rodrigues' formula.
- `bend_branches(direction, degrees, plane=(0.0, 1.0, 0.0))` — Both directions `degrees` off `direction`: the two branches of one hinge angle.
- `frame(aim, chest)` — A torso's (superior, right, anterior) axes, from where it points and faces.
- `spanning(near, far, degrees)` — How far apart a two-bone chain's ends sit with its middle joint at `degrees`.
- `two_bone(root, target, near, far, bend_hint)` — The middle joint of a two-bone chain reaching from `root` to `target`.
- `joint_on_floor(root, heading, length, height=0.0)` — One bone's far joint, landing on the floor — or at `height` — along `heading`.
- `middle_joint_on_floor(root, target, near, far, outward=(1.0, 0.0, 0.0), height=0.0)` — The middle joint of a two-bone chain, resting on the floor — or at `height`.
- `end_joint_on_floor(root, middle, length, degrees, forward=(0.0, 0.0, 1.0), height=0.0)` — The far joint of a two-bone chain, landing at `height`, bent `degrees` at the middle.
- `folded_knee_on_floor(root, heading, near, far, outward=(1.0, 0.0, 0.0), degrees=90.0)` — A folded knee and the joint past it, both on the floor, shin along `heading`.
- `tucked_foot(ankle, anterior, superior, dorsiflexion=15.0, ankle_to_ball=0.13, ankle_above_sole=0.07, toes=0.06)` — The ball and the tucked toetip of a foot standing on its toes.
- `shoulders_at_height(thoracic, superior, half_width, height, toward=(1.0, 0.0, 0.0))` — Both shoulders of a girdle rolled about the spine until one is at `height`.
- `limb(root, target, near, far, tip=None, bend_hint=(0.0, 0.0, -1.0))` — A whole limb: the middle joint, the end joint, and the tip beyond it.
- `straight_limb(root, direction, near, far, tip=None)` — A limb locked straight out from `root` along `direction`, with no bend to solve.
- `solve(low, high, f, target, steps=200)` — The input between `low` and `high` where `f` returns `target`, by bisection.

## `skeleton`

Checks every pose package needs before it is done.

- `LANDMARKS` — constant
- `LIMB_BONES` — constant
- `HINGES` — constant
- `SPINE` — constant
- `FEET` — constant
- `FEET_AND_HANDS` — constant
- `SEGMENT_MASSES` — constant
- `distance(a, b)` — Straight-line distance between two [x, y, z] points, in meters.
- `angle_at(joint, a, b)` — Interior angle at `joint` between the bones running to `a` and `b`.
- `angle_between(u, v)` — Angle between two free vectors, in degrees.
- `tilt(a, b)` — Signed degrees the segment from `a` to `b` rises above the floor plane.
- `joint_angles(pose, hinges=HINGES)` — Every hinge joint's interior angle, as {landmark: degrees}.
- `hip_flexion(pose, side, trunk=('TOP_OF_THORACIC_SPINE', 'BOTTOM_OF_SACRAL_SPINE'))` — Degrees a hip is folded, measured against the trunk axis.
- `chain_turns(points)` — Each turn along a chain of landmarks, as the axis it turns about.
- `axial_rotation(pose, upper=('LEFT_SHOULDER', 'RIGHT_SHOULDER'), lower=('LEFT_HIP', 'RIGHT_HIP'), axis=('BOTTOM_OF_SACRAL_SPINE', 'TOP_OF_THORACIC_SPINE'))` — Signed degrees `upper` is turned against `lower` about `axis`.
- `point_to_bone(point, a, b)` — Distance from a landmark to the bone drawn between `a` and `b`.
- `bone_gap(pose, first, second, samples=48)` — Closest approach between two named bones, in meters.
- `bone_contact(pose, first, second, samples=192)` — Where two bones come nearest: the gap, and how far along each that is.
- `center_of_mass(pose, segments=SEGMENT_MASSES)` — Where the figure's weight acts, as an [x, y, z] point.
- `sole_footprint(ankle, toe, heel_behind=0.07, half_width=0.045, toe_half_width=0.036)` — The corners of one foot's sole on the floor, ready for `base_of_support`.
- `toe_footprint(ankle, toe, ball_behind=0.075, half_width=0.042, toe_half_width=0.036)` — The corners of the ball-and-toes patch a lifted-heel foot stands on.
- `palm_footprint(wrist, fingertip, heel_behind=0.025, half_width=0.045, finger_half_width=0.038)` — The corners of one flat hand on the floor, ready for `base_of_support`.
- `sit_bones(left_hip, right_hip, superior=(0.0, 1.0, 0.0), drop=0.085, back=0.035, half_width=0.06)` — Where a seated figure's two sit bones touch, ready for `base_of_support`.
- `heel(ankle, toe, back=0.045, deep=0.045)` — Where one foot's heel is, which no landmark names, from its ankle and toe.
- `base_of_support(contacts)` — The footprint of some ground contacts: their convex hull as (x, z) corners.
- `over_base_of_support(point, contacts)` — Does `point` fall within the footprint of `contacts`, seen from above?
- `support_margin(point, contacts)` — How far inside the footprint of `contacts` a point falls, in meters.
- `validate_landmarks(pose, stem=None)` — All 20 landmarks present and numeric; optionally `pose` matches `stem`.
- `check_limb_symmetry(pose, tolerance=0.001)` — Left and right limb lengths agree to within `tolerance` meters.
- `check_floor_contact(pose, contacts=FEET, clearance=0.05, tolerance=1e-06)` — The named landmarks rest on the floor and nothing else reaches it.
- `check_resting_heights(pose, ceilings, floor_tolerance=1e-06)` — No landmark below the floor, and each named one no higher than its ceiling.
- `bones()` — Every bone the viewer draws, as (parent, child) landmark names.
- `check_bone_clearance(pose, limit=0.05, touching=(), touching_limit=0.03)` — No two bones that do not share a joint come within `limit` meters.
- `check_origin(pose, tolerance=1e-06)` — The origin is the floor point beneath the LEFT_HIP/RIGHT_HIP midpoint.