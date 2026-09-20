"""Place a limb by saying where it should reach, not by typing coordinates.

A pose file is 20 points, and the tempting way to write one is to type numbers
and nudge them until it looks right. That is how a figure ends up with a left
forearm 8 mm longer than its right: `skeleton.check_limb_symmetry` then reports
a mismatch that is a typo rather than anything about the pose.

Building the figure instead of typing it removes the whole class of problem.
Choose where a hand or foot goes and let `two_bone` solve the joint between, and
the bone lengths come out exactly as specified — the symmetry check passes by
construction, and a pose is edited by moving a target rather than by correcting
drift across six numbers.

    from utils import rig

    elbow = rig.two_bone(shoulder, wrist, upper_arm, forearm, bend_hint=[0, 0, -1])

Where the middle joint has to rest on the ground rather than merely bulge in a
direction — the folded knee of a seated pose — `middle_joint_on_floor` solves
for y=0 instead of guessing a hint:

    knee = rig.middle_joint_on_floor(hip, ankle, thigh, shin, outward=(-1, 0, 0))

Plain lists of three floats throughout, matching the pose files. Meters, Y-up.
"""

import math


def add(a, b):
    """a + b."""
    return [a[i] + b[i] for i in range(3)]


def sub(a, b):
    """a - b."""
    return [a[i] - b[i] for i in range(3)]


def scale(a, k):
    """a * k."""
    return [a[i] * k for i in range(3)]


def dot(a, b):
    """The dot product of a and b."""
    return sum(a[i] * b[i] for i in range(3))


def cross(a, b):
    """The cross product of a and b, right-handed like the pose files."""
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def norm(a):
    """The length of a."""
    return math.sqrt(dot(a, a))


def unit(a):
    """a scaled to length 1. Raises on the zero vector."""
    length = norm(a)
    if length == 0:
        raise ValueError("cannot take the direction of a zero-length vector")
    return scale(a, 1.0 / length)


def lerp(a, b, t):
    """The point `t` of the way from a to b, so `t=0` is a and `t=1` is b."""
    return [a[i] + (b[i] - a[i]) * t for i in range(3)]


def along(root, target, length):
    """The point `length` meters from `root` toward `target`.

    A straight limb's middle joint: a locked knee sits one thigh along the line
    from the hip to the ankle, and lands there exactly rather than nearly.
    """
    return add(root, scale(unit(sub(target, root)), length))


def lean(degrees, toward=(1.0, 0.0, 0.0)):
    """A unit vector `degrees` off straight up, tipped `toward` a direction.

    How a spine is written: a chain of leans that grows up the back gives a
    curve, where one lean applied at the hip gives a plank tipped over.
    `toward` is flattened against vertical first, so any horizontal-ish
    direction works — `(1, 0, 0)` leans to the practitioner's right, `(0, 0, 1)`
    leans forward.
    """
    flat = [toward[0], 0.0, toward[2]]
    if norm(flat) == 0:
        raise ValueError("`toward` must have a horizontal component to lean along")
    flat = unit(flat)
    radians = math.radians(degrees)
    return add(scale(flat, math.sin(radians)), [0.0, math.cos(radians), 0.0])


def rotate(v, axis, degrees):
    """`v` turned `degrees` about `axis`, right-handed, by Rodrigues' formula.

    `lean` tips a direction off vertical and `frame` builds a torso's axes, but
    a twist turns something about an axis that is neither vertical nor one of
    the world's: a revolved pose rotates the ribcage about the spine, which is
    already tilted. That is what this does.

    `axis` need not be a unit vector; it is normalised first. `degrees` is
    counter-clockwise looking back down the axis toward the origin, so it
    agrees with the sign `skeleton.axial_rotation` reports:

        chest = rig.rotate(pelvis_anterior, sacrum_to_head, 60.0)
        superior, right, anterior = rig.frame(sacrum_to_head, chest)

    Raises on a zero-length `axis`, which names no rotation.
    """
    u = unit(axis)
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return add(add(scale(v, c), scale(cross(u, v), s)),
               scale(u, dot(u, v) * (1.0 - c)))


def bend_branches(direction, degrees, plane=(0.0, 1.0, 0.0)):
    """Both directions `degrees` off `direction`: the two branches of one hinge angle.

    `end_joint_on_floor` and `folded_knee_on_floor` bend a chain that has the
    ground to land on. A limb in the air has nothing to solve against — a supine
    tuck, a raised leg, an arm overhead is given as joint angles instead — and an
    interior angle alone does not say which way the joint folds. Turning
    `direction` by `+degrees` and by `-degrees` about the hinge axis gives both
    answers; the caller picks, since which one is the pose is the caller's to
    know and not something a shared helper can decide:

        knee_to_hip = rig.unit(rig.sub(hip, knee))
        folded, back = rig.bend_branches(knee_to_hip, 55.0)
        # of the two, the one that sends the ankle toward the feet; the other
        # drives it into the torso
        shin = folded if folded[2] >= back[2] else back
        ankle = rig.along(knee, rig.add(knee, shin), 0.41)

    `direction` runs from the hinge back along the bone that arrives at it, so
    `degrees` comes out as the interior angle `skeleton.joint_angles` reports.
    The hinge axis is the one perpendicular to both `direction` and `plane`, so
    the bend happens in the plane they span: `plane` defaults to `+Y`, which
    swings a bone the body's own way for a figure whose limb is roughly sagittal.
    Raises if `plane` is parallel to `direction`, which names no hinge axis.
    """
    axis = cross(direction, plane)
    if norm(axis) == 0:
        raise ValueError("`plane` is parallel to `direction`, so it names no hinge axis")
    axis = unit(axis)
    return rotate(direction, axis, degrees), rotate(direction, axis, -degrees)


def frame(aim, chest):
    """A torso's (superior, right, anterior) axes, from where it points and faces.

    `lean` tips one direction off vertical, which places a spine but not the
    landmarks that hang off its sides. A shoulder or a hip is a step along the
    body's own left-right axis, and once a pose tips or twists, that axis is no
    longer world `+X`:

        superior, right, anterior = rig.frame(sacrum_to_head, chest_direction)
        left_shoulder = rig.sub(thoracic, rig.scale(right, 0.17))

    `aim` runs sacrum to head and `chest` is roughly where the front of the body
    faces; `chest` need only be approximate, since it is squared against `aim`
    rather than trusted. The three come back orthonormal and right-handed, so
    they agree with the project's frame: standing upright and facing `+Z` gives
    back `+Y`, `+X`, `+Z`. Raises if `chest` is parallel to `aim`, which names
    no facing.
    """
    superior = unit(aim)
    forward = sub(chest, scale(superior, dot(chest, superior)))
    if norm(forward) == 0:
        raise ValueError("`chest` is parallel to `aim`, so it names no facing")
    anterior = unit(forward)
    return superior, unit(cross(superior, anterior)), anterior


def spanning(near, far, degrees):
    """How far apart a two-bone chain's ends sit with its middle joint at `degrees`.

    The inverse of `skeleton.angle_at`, and the piece `two_bone` assumes you
    already have: that one takes a target and gives back the middle joint, so
    something has to decide where the target goes. A limb the pose calls
    straight is the common case, and typing the span by hand is how a knee ends
    up at 163 degrees in a figure meant to be standing on it:

        straight = rig.spanning(thigh, shin, 177.0)
        ankle = rig.joint_on_floor(hip, lean, straight, height=0.075)

    177 rather than 180 because a leg locked dead straight reads as
    hyperextended, and because `two_bone` cannot bend a chain that has no room
    to. Pass the angle the pose wants and the span follows.

    `degrees` is the interior angle at the middle joint, the same convention
    `skeleton.joint_angles` reports, so a span from here and an angle measured
    back off the finished landmarks agree. Raises outside 0-180, which names no
    joint.
    """
    if not 0.0 <= degrees <= 180.0:
        raise ValueError(f"{degrees} is not an interior angle between 0 and 180 degrees")
    return math.sqrt(max(0.0, near * near + far * far
                         - 2 * near * far * math.cos(math.radians(degrees))))


def two_bone(root, target, near, far, bend_hint):
    """The middle joint of a two-bone chain reaching from `root` to `target`.

    `near` is the bone length at the root (upper arm, thigh) and `far` the one
    at the target (forearm, shin); both come out exact. A chain has a circle of
    valid solutions, so `bend_hint` picks one: it is the direction the joint
    should bulge toward, and only its component across the chain is used, so a
    rough direction such as `[0, 0, -1]` for "elbow points back" is enough.

    Raises ValueError if `target` is out of reach or too close to fold into,
    since either means the caller wanted a different target rather than a
    silently straightened limb.
    """
    chain = sub(target, root)
    span = norm(chain)
    if span > near + far:
        raise ValueError(
            f"target is {span:.4f} m from the root, past the {near + far:.4f} m reach")
    if span < abs(near - far):
        raise ValueError(
            f"target is {span:.4f} m from the root, inside the {abs(near - far):.4f} m "
            f"the chain can fold to")
    if span == 0:
        raise ValueError("target coincides with the root")
    axis = scale(chain, 1.0 / span)

    # Where the joint projects onto the chain, and how far off it sits.
    projection = (near * near - far * far + span * span) / (2 * span)
    offset = math.sqrt(max(0.0, near * near - projection * projection))

    across = sub(bend_hint, scale(axis, dot(bend_hint, axis)))
    if norm(across) == 0:
        raise ValueError("bend_hint runs along the chain, so it picks no direction")
    return add(add(root, scale(axis, projection)), scale(unit(across), offset))


def joint_on_floor(root, heading, length, height=0.0):
    """One bone's far joint, landing on the floor — or at `height` — along `heading`.

    The simplest of the three grounded cases, and the one the others cannot do:
    a leg reaching back until its knee meets the ground, where the shin then goes
    wherever the pose sends it. `middle_joint_on_floor` wants the ankle's
    position to solve between, and `folded_knee_on_floor` wants the shin flat on
    the floor as well; this wants neither, only a direction to travel.

    `heading` is flattened against vertical, so any horizontal-ish direction
    works. The bone spans the difference in height, so its horizontal reach is
    what is left of `length` — which is why the joint lands at exactly the height
    asked for rather than near it.

    `height` is where the joint comes to rest, 0 being the floor itself. That is
    where a toetip or a fingertip goes, because the landmark is at the skin. A
    limb lying along the ground rests on its flesh instead, and its *joint
    centres* stay a few centimetres up — a supine knee around 0.07, an elbow
    around 0.05 — which is the same question `skeleton.check_resting_heights`
    asks of a finished pose. Pass the height and this solves for it.

    Raises ValueError if the bone is too short to span the gap at all, since a
    caller wanting a joint at a height has asked for a root that does not allow
    one.
    """
    flat = [heading[0], 0.0, heading[2]]
    if norm(flat) == 0:
        raise ValueError("`heading` must have a horizontal component to travel along")
    drop = root[1] - height
    if length < abs(drop):
        raise ValueError(
            f"the root is {abs(drop):.4f} m from y={height:.4f}, past the "
            f"{length:.4f} m bone, so it cannot reach")
    flat = unit(flat)
    reach = math.sqrt(max(0.0, length * length - drop * drop))
    return [root[0] + flat[0] * reach, height, root[2] + flat[2] * reach]


def middle_joint_on_floor(root, target, near, far, outward=(1.0, 0.0, 0.0),
                          height=0.0):
    """The middle joint of a two-bone chain, resting on the floor — or at `height`.

    A knee that comes down onto the ground: the deeply folded leg of a seated
    pose, where the bone lengths are fixed and the knee has to land at exactly
    y=0 rather than near it. `two_bone`'s `bend_hint` cannot say that — the
    joint lies on a circle of valid positions, and a rough direction picks a
    point on it without knowing its height. Two points of that circle touch the
    floor, so this solves for them and `outward` chooses between them: the one
    further along that horizontal direction, `(-1, 0, 0)` for a knee opening to
    the practitioner's left.

    `height` is where the joint comes to rest, and means what it means in
    `joint_on_floor`: 0 is the floor itself, which is where a toetip or a
    fingertip goes because the landmark is at the skin. A knee or an elbow lying
    on the ground rests on its flesh instead, and its *joint centre* stays a few
    centimetres up — a dropped knee around 0.07, an elbow around 0.05 — which is
    what `skeleton.check_resting_heights` asks of a finished pose. Pass the
    height and this solves the joint circle for it rather than for zero.

    The hint it solves for is handed straight to `two_bone`, so the bone lengths
    come out exact the same way. Raises ValueError if the chain cannot reach that
    height in any position, since a caller wanting a grounded joint has asked for
    a root or target that does not allow one.
    """
    chain = sub(target, root)
    span = norm(chain)
    if span == 0:
        raise ValueError("target coincides with the root")
    axis = scale(chain, 1.0 / span)
    projection = (near * near - far * far + span * span) / (2 * span)
    radius = math.sqrt(max(0.0, near * near - projection * projection))
    centre = add(root, scale(axis, projection))

    # An orthonormal pair spanning the circle's plane.
    seed = [0.0, 1.0, 0.0] if abs(axis[1]) < 0.9 else [1.0, 0.0, 0.0]
    u = unit(sub(seed, scale(axis, dot(seed, axis))))
    v = [axis[1] * u[2] - axis[2] * u[1],
         axis[2] * u[0] - axis[0] * u[2],
         axis[0] * u[1] - axis[1] * u[0]]

    # Solve radius * (cos t * u.y + sin t * v.y) = height - centre.y for t.
    a, b = radius * u[1], radius * v[1]
    rise = height - centre[1]
    reach = math.hypot(a, b)
    if reach < abs(rise):
        raise ValueError(
            f"this chain cannot put its middle joint at y={height:.4f}: the joint "
            f"circle spans y={centre[1] - reach:.4f} to y={centre[1] + reach:.4f}")
    phase = math.atan2(b, a)
    spread = math.acos(max(-1.0, min(1.0, rise / reach)))

    hints = [[math.cos(phase + side * spread) * u[i]
              + math.sin(phase + side * spread) * v[i] for i in range(3)]
             for side in (1.0, -1.0)]
    return two_bone(root, target, near, far,
                    max(hints, key=lambda hint: dot(hint, list(outward))))


def end_joint_on_floor(root, middle, length, degrees,
                       forward=(0.0, 0.0, 1.0), height=0.0):
    """The far joint of a two-bone chain, landing at `height`, bent `degrees` at the middle.

    `middle_joint_on_floor` read the other way round. There the ankle's position
    is known and the knee is solved between; here the knee is already placed —
    by a hip angle, say — and what the pose states is how deeply it is bent. The
    foot then has to reach the ground at exactly that bend, which is what a
    lunge or a squat is described by: "front knee at ninety degrees, foot flat".

    `degrees` is the interior angle at the middle joint, the same number
    `skeleton.joint_angles` reads back, so a pose built for 93 measures 93.
    The far joint lies on a circle about `middle`, and the two points of it at
    the right height and the right bend are the intersection of two circles in
    the plane y=`height`; `forward` chooses between them, taking the one further
    along that horizontal direction.

    `height` is where the joint comes to rest, 0 being the floor itself — a
    toetip or a fingertip, where the landmark is at the skin. An ankle above a
    flat foot sits a foot's thickness up, around 0.08, and passing that puts the
    sole on the ground rather than the joint centre.

    Raises ValueError when no such joint exists: the bend and the height
    together can be unreachable, and the degenerate case — the middle joint
    exactly `length` above the height, so the far joint can only hang straight
    down — is one the caller should hear about rather than get a silent nudge.
    """
    near = norm(sub(middle, root))
    chord = math.sqrt(max(0.0, near * near + length * length
                          - 2.0 * near * length * math.cos(math.radians(degrees))))

    drop_middle = middle[1] - height
    drop_root = root[1] - height
    if abs(drop_middle) > length:
        raise ValueError(
            f"the middle joint is {abs(drop_middle):.4f} m from y={height:.4f}, "
            f"past the {length:.4f} m bone, so it cannot reach")
    if abs(drop_root) > chord:
        raise ValueError(
            f"a {degrees:.1f} degree bend spans {chord:.4f} m, which cannot "
            f"reach from the root down to y={height:.4f}")

    radius_middle = math.sqrt(max(0.0, length * length - drop_middle * drop_middle))
    radius_root = math.sqrt(max(0.0, chord * chord - drop_root * drop_root))
    span = math.hypot(root[0] - middle[0], root[2] - middle[2])
    if (span > radius_middle + radius_root
            or span < abs(radius_middle - radius_root) or span == 0):
        raise ValueError(
            f"no far joint at y={height:.4f} bends the middle joint to "
            f"{degrees:.1f} degrees: the two circles that would meet there do not")

    east = [(root[0] - middle[0]) / span, (root[2] - middle[2]) / span]
    along = (span * span + radius_middle * radius_middle
             - radius_root * radius_root) / (2.0 * span)
    off = math.sqrt(max(0.0, radius_middle * radius_middle - along * along))

    solutions = [[middle[0] + along * east[0] - side * off * east[1],
                  height,
                  middle[2] + along * east[1] + side * off * east[0]]
                 for side in (1.0, -1.0)]
    return max(solutions,
               key=lambda p: p[0] * forward[0] + p[2] * forward[2])


def folded_knee_on_floor(root, heading, near, far,
                         outward=(1.0, 0.0, 0.0), degrees=90.0):
    """A folded knee and the joint past it, both on the floor, shin along `heading`.

    The other way a grounded knee is described. `middle_joint_on_floor` wants the
    ankle's position and solves for the knee between; a kneeling pose often says
    the opposite — the shin lies flat on the floor pointing somewhere, the knee
    is bent this far, and the ankle ends up wherever that puts it. Frog Pose is
    the case: each shin runs backward and a little outward along the ground with
    a right angle at the knee, and the ankle is a consequence, not an input.

    `root` is the hip, `heading` the direction the shin runs — flattened against
    vertical, so any horizontal-ish direction works — `near` the thigh and `far`
    the shin. `degrees` is the angle held at the knee, 90 by default. Returns
    `(knee, end)`: both sit at y=0 and the bone lengths come out exact.

    Two positions satisfy any such request, mirrored across the shin's line, so
    `outward` chooses between them the way it does in `middle_joint_on_floor`:
    the knee further along that horizontal direction, `(-1, 0, 0)` for one
    opening to the practitioner's left.

    Raises ValueError if no position works — the root too high above the floor
    for the thigh to reach it at that angle, which means the caller wants a lower
    root or a more open knee rather than a knee left hovering.
    """
    flat = [heading[0], 0.0, heading[2]]
    if norm(flat) == 0:
        raise ValueError("`heading` must have a horizontal component for the shin "
                         "to run along")
    shin = unit(flat)
    across = [-shin[2], 0.0, shin[0]]

    # The knee is on the floor, so the thigh spans the root's height: its
    # horizontal offset from the root has length sqrt(near^2 - height^2). Split
    # that offset along the shin and across it — the angle at the knee fixes the
    # component along, and what is left goes across.
    # Squared lengths, so the exactly-flush cases — a vertical thigh, a knee at
    # the limit of its angle — land a rounding error either side of zero. Inside
    # that margin the position is the degenerate one, not an impossible one.
    tolerance = 1e-9

    height = root[1]
    span = near * near - height * height
    if span < -tolerance:
        raise ValueError(
            f"the root is {height:.4f} m up, past the {near:.4f} m thigh, so no "
            f"knee position reaches the floor")
    along_shin = -near * math.cos(math.radians(degrees))
    sideways = span - along_shin * along_shin
    if sideways < -tolerance:
        raise ValueError(
            f"a {degrees:.1f} degree knee cannot put a {near:.4f} m thigh from "
            f"y={height:.4f} onto the floor along this heading")
    sideways = math.sqrt(max(0.0, sideways))

    offsets = [add(scale(shin, along_shin), scale(across, side * sideways))
               for side in (1.0, -1.0)]
    offset = max(offsets, key=lambda candidate: dot(candidate, list(outward)))
    knee = [root[0] + offset[0], 0.0, root[2] + offset[2]]
    return knee, add(knee, scale(shin, far))


def tucked_foot(ankle, anterior, superior, dorsiflexion=15.0,
                ankle_to_ball=0.130, ankle_above_sole=0.070, toes=0.060):
    """The ball and the tucked toetip of a foot standing on its toes.

    `skeleton.heel` answers where a flat foot's heel is. This is the other
    foot: toes tucked under, heel lifted, the weight on the ball — a plank, a
    prone hover, an upward-facing dog, the back foot of a lunge.

    That foot is the one nobody can place by eye. `CENTRAL_TOETIP` is the tip
    of the toes, and tucking folds them nearly double against the metatarsals,
    so the straight line the viewer draws from the ankle to the toetip is both
    much shorter than a foot and much steeper than the foot's own axis. Guess
    it and the toetip lands too far from the ankle, which reads at the ankle
    as an angle no ankle reaches.

    So build the foot instead of the landmark. `anterior` is the direction the
    body's front faces and `superior` the direction of its head — the axes
    `frame` hands back. Off those, the foot turns `dorsiflexion` degrees from
    neutral, which tips its long axis from anterior toward the shin and turns
    the sole with it; the ball sits `ankle_to_ball` along that axis and
    `ankle_above_sole` toward the sole; and the toes, bent up at the joints
    until they lie flat, run `toes` further along the ground the way the foot
    points. The defaults describe a 0.25 m foot at an everyday dorsiflexion.

    Returns `(ball, toetip)`, both as [x, y, z]. The toetip shares the ball's
    height, because tucked toes lie in the same plane the ball rests on — so
    the pose is grounded exactly when the ball is, and neither point is forced
    onto the floor here. Solve for that: the ankle comes from the leg, and the
    body's tilt is what lands the foot.

        ball, toetip = rig.tucked_foot(ankle, anterior, superior)
        tilt = rig.solve(2.0, 20.0, lambda t: -ball_height(t), 0.0)

    Raises when `anterior` and `superior` are the same direction, or when the
    foot points straight down and so has no heading for the toes to follow.
    """
    superior = unit(superior)
    anterior = sub(anterior, scale(superior, dot(anterior, superior)))
    if norm(anterior) < 1e-9:
        raise ValueError(
            "anterior and superior point the same way, so the foot has no "
            "direction to face")
    anterior = unit(anterior)

    turn = math.radians(dorsiflexion)
    # Dorsiflexion tips the foot's long axis from anterior toward the shin.
    # The sole faces away from the shin at neutral and rides the same turn.
    foot_axis = add(scale(anterior, math.cos(turn)), scale(superior, math.sin(turn)))
    sole_dir = add(scale(superior, -math.cos(turn)), scale(anterior, math.sin(turn)))

    ball = add(add(list(ankle), scale(foot_axis, ankle_to_ball)),
               scale(sole_dir, ankle_above_sole))

    # The toes are bent up at the joints until they lie flat, so they leave the
    # ball along the floor rather than along the foot: the heading is the
    # foot's axis with its rise taken out.
    heading = [foot_axis[0], 0.0, foot_axis[2]]
    if norm(heading) < 1e-9:
        raise ValueError(
            f"a foot at {dorsiflexion:.1f} degrees points straight down, so "
            f"the tucked toes have no heading to lie along")
    return ball, add(ball, scale(unit(heading), toes))


def shoulders_at_height(thoracic, superior, half_width, height,
                        toward=(1.0, 0.0, 0.0)):
    """Both shoulders of a girdle rolled about the spine until one is at `height`.

    The same request as `joint_on_floor` and its neighbours, asked about the
    shoulder girdle: a pose that rests a shoulder on the mat — a side-lying
    twist, Thread the Needle, anything that rolls the chest over — knows how low
    that shoulder sits and not where either shoulder is. Typing the two points
    is what puts them the wrong distance apart, which the girdle never is.

    The girdle is the bar through `thoracic`, `half_width` either side of it and
    perpendicular to `superior`, the spine's direction at that joint. It is
    rolled about the spine until the end that started out along `toward` has
    dropped to `height`, and the other end follows it round. Returns
    `(dropped, raised)`, the two shoulder positions.

    How far a roll can lower a shoulder depends on the spine it turns about: the
    girdle stays perpendicular to it, so a spine already diving toward the floor
    leaves the girdle less of the vertical to work with, and a vertical spine
    leaves it none at all. Raises ValueError when `height` is past that reach,
    or above where the shoulder already sits unrolled, rather than returning a
    girdle that does not meet the request.
    """
    superior = unit(superior)
    neutral = unit(sub(toward, scale(superior, dot(toward, superior))))

    # {neutral, normal, superior} is orthonormal, so the y a roll can move the
    # girdle through is whatever the spine's own y leaves over.
    span = half_width * math.sqrt(max(0.0, 1.0 - superior[1] * superior[1]))
    if height < thoracic[1] - span - 1e-9:
        raise ValueError(
            f"a shoulder {half_width:.4f} m out cannot reach y={height:.4f}: "
            f"rolled as far as this spine allows it bottoms out at "
            f"y={thoracic[1] - span:.4f}")

    normal = cross(superior, neutral)
    unrolled = thoracic[1] + neutral[1] * half_width
    if height > unrolled + 1e-9:
        raise ValueError(
            f"that shoulder already sits at y={unrolled:.4f}, below the requested "
            f"y={height:.4f}; this rolls a girdle down, not up")
    if abs(unrolled - height) < 1e-12:
        return (add(thoracic, scale(neutral, half_width)),
                sub(thoracic, scale(neutral, half_width)))

    # Roll the way that lowers the `toward` end rather than lifting it: rotating
    # by `degrees` sweeps the girdle along `normal`, so its sign says which way.
    sense = -1.0 if normal[1] > 0.0 else 1.0

    # Where that roll has the shoulder at its lowest. Bisecting only as far as
    # there keeps the search on the arc the height falls across, since past it
    # the shoulder starts climbing the other side and the answer stops being
    # unique.
    lowest = math.degrees(math.atan2(sense * normal[1], neutral[1])) + 180.0

    def dropped_height(degrees):
        return add(thoracic,
                   scale(rotate(neutral, superior, sense * degrees),
                         half_width))[1]

    roll = solve(0.0, lowest, dropped_height, height)
    axis = rotate(neutral, superior, sense * roll)
    return (add(thoracic, scale(axis, half_width)),
            sub(thoracic, scale(axis, half_width)))


def limb(root, target, near, far, tip=None, bend_hint=(0.0, 0.0, -1.0)):
    """A whole limb: the middle joint, the end joint, and the tip beyond it.

    Arms and legs in this project are three bones, not two — the hand carries on
    past the wrist to a fingertip, the foot past the ankle to a toetip. Give the
    fingertip or toetip as `target` and the third bone's length as `tip`, and
    the hand is taken to lie along the line out from the root, which holds for a
    limb that is reaching rather than cocked back at the wrist.

    Returns `(middle, end, target)` — elbow, wrist, fingertip for an arm; knee,
    ankle, toetip for a leg. With `tip=None` it is `two_bone` plus the target,
    so the end joint *is* the target.
    """
    end = target if tip is None else sub(target, scale(unit(sub(target, root)), tip))
    return two_bone(root, end, near, far, list(bend_hint)), end, target


def straight_limb(root, direction, near, far, tip=None):
    """A limb locked straight out from `root` along `direction`, with no bend to solve.

    The companion to `limb` for the case where the limb is not reaching for a
    point but held out along a line — arms straight overhead beside the ears, a
    leg lifted along a heading. Say which way it points and the bones follow, so
    nothing has to solve for a middle joint that is simply on the way.

    Returns `(middle, end)`, or `(middle, end, tip_point)` when `tip` is given —
    the same joints, in the same order, as `limb`.
    """
    heading = unit(list(direction))
    joints = [add(root, scale(heading, near)),
              add(root, scale(heading, near + far))]
    if tip is not None:
        joints.append(add(root, scale(heading, near + far + tip)))
    return tuple(joints)


def solve(low, high, f, target, steps=200):
    """The input between `low` and `high` where `f` returns `target`, by bisection.

    Everything else here places a limb from an angle, and a pose is rarely
    described by one. It is described by where something has to end up: a leg
    rides as high as the arm holding its foot can still reach, a standing leg
    tilts in as far as it takes to bring the centre of mass over the foot.
    Typing an angle for that is guessing at it, and the guess is what a viewer
    shows. Write the measurement instead and solve for the angle that hits it:

        elevation = rig.solve(0.0, 60.0, lambda d: -reach_at(d), -arm_length)

    `f` takes one number and returns one number, and must be monotonic across
    the bracket — negate it, as above, if it falls where you want it to rise.
    `target` must lie between `f(low)` and `f(high)`; a bracket that does not
    contain the answer raises rather than quietly returning one of its ends,
    since the caller wanted a different bracket. `steps` is halvings, and the
    default is well past what a float can still distinguish.
    """
    at_low, at_high = f(low), f(high)
    if not min(at_low, at_high) <= target <= max(at_low, at_high):
        raise ValueError(
            f"target {target:.4f} is outside f({low:.4f})={at_low:.4f} .. "
            f"f({high:.4f})={at_high:.4f}, so the bracket does not contain it")

    rising = at_high > at_low
    for _ in range(steps):
        mid = 0.5 * (low + high)
        if (f(mid) < target) == rising:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)
