"""Checks every pose package needs before it is done.

The project's conventions in code: the 20 landmark names, the bones drawn
between them, meters / Y-up / origin on the floor beneath the hip midpoint.

Each check returns a list of human-readable problems and is empty when the
pose is good, so a caller can gather several and print them all:

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from utils import skeleton  # noqa: E402

    pose = json.loads(path.read_text())
    problems = skeleton.validate_landmarks(pose, stem=path.stem)
    problems += skeleton.check_limb_symmetry(pose)
    problems += skeleton.check_floor_contact(pose, contacts=skeleton.FEET)
    problems += skeleton.check_bone_clearance(pose)
    problems += skeleton.check_origin(pose)
"""

import itertools
import math

#: The 20 landmark names, in the order the pose files list them.
LANDMARKS = (
    "TOP_OF_HEAD", "TOP_OF_THORACIC_SPINE", "TOP_OF_LUMBAR_SPINE",
    "BOTTOM_OF_SACRAL_SPINE",
    "LEFT_SHOULDER", "RIGHT_SHOULDER", "LEFT_ELBOW", "RIGHT_ELBOW",
    "LEFT_WRIST", "RIGHT_WRIST",
    "LEFT_CENTRAL_FINGERTIP", "RIGHT_CENTRAL_FINGERTIP",
    "LEFT_HIP", "RIGHT_HIP", "LEFT_KNEE", "RIGHT_KNEE",
    "LEFT_ANKLE", "RIGHT_ANKLE", "LEFT_CENTRAL_TOETIP", "RIGHT_CENTRAL_TOETIP",
)

#: The bilateral bones, named without their LEFT_/RIGHT_ prefix.
LIMB_BONES = (
    ("SHOULDER", "ELBOW"), ("ELBOW", "WRIST"), ("WRIST", "CENTRAL_FINGERTIP"),
    ("HIP", "KNEE"), ("KNEE", "ANKLE"), ("ANKLE", "CENTRAL_TOETIP"),
)

#: Each hinge joint and the two landmarks its bones run to, named without their
#: LEFT_/RIGHT_ prefix.
HINGES = (
    ("ELBOW", "SHOULDER", "WRIST"),
    ("WRIST", "ELBOW", "CENTRAL_FINGERTIP"),
    ("KNEE", "HIP", "ANKLE"),
    ("ANKLE", "KNEE", "CENTRAL_TOETIP"),
)

#: The midline chain, base to crown.
SPINE = (
    "BOTTOM_OF_SACRAL_SPINE", "TOP_OF_LUMBAR_SPINE",
    "TOP_OF_THORACIC_SPINE", "TOP_OF_HEAD",
)

# Handy floor-contact sets for the common cases.
FEET = ("LEFT_CENTRAL_TOETIP", "RIGHT_CENTRAL_TOETIP")
FEET_AND_HANDS = FEET + ("LEFT_CENTRAL_FINGERTIP", "RIGHT_CENTRAL_FINGERTIP")

#: Each segment's share of body weight, with the two landmarks whose midpoint
#: stands in for where that share acts. Standard cadaver-derived fractions;
#: they sum to 1.
SEGMENT_MASSES = (
    (0.081, "TOP_OF_THORACIC_SPINE", "TOP_OF_HEAD"),
    (0.497, "BOTTOM_OF_SACRAL_SPINE", "TOP_OF_THORACIC_SPINE"),
    (0.028, "LEFT_SHOULDER", "LEFT_ELBOW"), (0.028, "RIGHT_SHOULDER", "RIGHT_ELBOW"),
    (0.016, "LEFT_ELBOW", "LEFT_WRIST"), (0.016, "RIGHT_ELBOW", "RIGHT_WRIST"),
    (0.006, "LEFT_WRIST", "LEFT_CENTRAL_FINGERTIP"),
    (0.006, "RIGHT_WRIST", "RIGHT_CENTRAL_FINGERTIP"),
    (0.100, "LEFT_HIP", "LEFT_KNEE"), (0.100, "RIGHT_HIP", "RIGHT_KNEE"),
    (0.0465, "LEFT_KNEE", "LEFT_ANKLE"), (0.0465, "RIGHT_KNEE", "RIGHT_ANKLE"),
    (0.0145, "LEFT_ANKLE", "LEFT_CENTRAL_TOETIP"),
    (0.0145, "RIGHT_ANKLE", "RIGHT_CENTRAL_TOETIP"),
)


def distance(a, b):
    """Straight-line distance between two [x, y, z] points, in meters."""
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def angle_at(joint, a, b):
    """Interior angle at `joint` between the bones running to `a` and `b`.

    In degrees, so 180 is a joint held straight and 90 a right angle. This is
    how a pose says what it actually is: a straight leg is a knee at 180, a
    warrior's front knee is one at 90, and a fold is a hip crease that has
    closed to something a hamstring can reach.
    """
    ja = distance(a, joint)
    jb = distance(b, joint)
    if ja == 0 or jb == 0:
        raise ValueError("cannot take an angle at a joint that coincides with an end")
    cosine = sum((a[i] - joint[i]) * (b[i] - joint[i]) for i in range(3)) / (ja * jb)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def angle_between(u, v):
    """Angle between two free vectors, in degrees.

    `angle_at` needs three landmarks meeting at a joint, which is fine for a
    hinge but not for everything a pose has to say. Some joints are not hinges
    and have no landmark of their own: the hip is measured as the trunk axis
    against the thigh, the shoulder as the trunk axis against the humerus. Pass
    those axes as vectors — `rig.sub` builds one from two landmarks — and read
    the opening off directly: 180 is a hip laid out straight in a reclined
    pose, 90 one folded to a right angle.
    """
    lu = math.sqrt(sum(c * c for c in u))
    lv = math.sqrt(sum(c * c for c in v))
    if lu == 0 or lv == 0:
        raise ValueError("cannot take an angle against a zero-length vector")
    cosine = sum(u[i] * v[i] for i in range(3)) / (lu * lv)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def tilt(a, b):
    """Signed degrees the segment from `a` to `b` rises above the floor plane.

    Positive is rising, negative is falling, and 0 is flat, whichever way the
    segment runs across the floor. `angle_at` measures two bones against each
    other; this measures one bone against the mat, which is how a pose says
    how far something has lifted: a forearm flat in sphinx is a tilt near 0, a
    trunk in a mild backbend one near 30, a vertical shin one near 90.
    """
    run = math.hypot(b[0] - a[0], b[2] - a[2])
    return math.degrees(math.atan2(b[1] - a[1], run))


def joint_angles(pose, hinges=HINGES):
    """Every hinge joint's interior angle, as {landmark: degrees}.

    `angle_at` measures one angle from three points the caller has looked up;
    this is the whole figure at once, because which two landmarks a hinge's bones
    run to is a fact about this skeleton rather than about any one pose. Both
    elbows, wrists, knees and ankles, in degrees, the same convention as
    `angle_at`: 180 is straight.

    Data, not a verdict — what range a pose is allowed to hold a joint in is the
    package's judgement, so the caller compares these against its own limits:

        tight = {name: angle for name, angle in skeleton.joint_angles(pose).items()
                 if angle < 35}

    Useful for the thing no other check here catches: a figure whose landmarks
    are all present, symmetric, grounded and clear of each other, and which is
    still folded somewhere a body does not fold.
    """
    landmarks = pose["landmarks"]
    angles = {}
    for side in ("LEFT", "RIGHT"):
        for joint, a, b in hinges:
            angles[f"{side}_{joint}"] = angle_at(
                landmarks[f"{side}_{joint}"],
                landmarks[f"{side}_{a}"], landmarks[f"{side}_{b}"])
    return angles


def hip_flexion(pose, side, trunk=("TOP_OF_THORACIC_SPINE",
                                   "BOTTOM_OF_SACRAL_SPINE")):
    """Degrees a hip is folded, measured against the trunk axis.

    The one joint `joint_angles` cannot report, because a hip is a ball rather
    than a hinge and has no third landmark to take an interior angle at. The
    angle at the sacrum is not a substitute: it reads about 110 degrees on a
    figure standing up and barely moves as the leg comes forward, so a pose can
    fold a hip past what a body allows without that number noticing.

    This measures the thigh against the direction the trunk runs downward, the
    way flexion is defined clinically. Zero is a leg in line with a standing
    trunk, 90 a thigh carried straight out in front of it, and 180 a full front
    split or a chest folded flat onto a straight leg — which is the limit, and
    one only a practised body reaches.

        fold = skeleton.hip_flexion(pose, "RIGHT")
        if fold > 175:
            problems.append("the front thigh has folded through the trunk")

    `trunk` is the axis to measure against, superior end first, for a pose that
    would rather use the lumbar spine or a rolled pelvis as its reference.
    Returns the angle; whether it is too much is the pose's own business.
    """
    landmarks = pose["landmarks"]
    superior, inferior = landmarks[trunk[0]], landmarks[trunk[1]]
    down = [inferior[i] - superior[i] for i in range(3)]
    thigh = [landmarks[f"{side}_KNEE"][i] - landmarks[f"{side}_HIP"][i]
             for i in range(3)]
    return angle_between(down, thigh)


def chain_turns(points):
    """Each turn along a chain of landmarks, as the axis it turns about.

    One vector per interior point: the cross product of the bone arriving at it
    with the bone leaving it. Its direction is the axis that turn happens
    about, by the right-hand rule, and its size grows with how sharp the turn
    is. `angle_at` says how much a chain bends and this says which way, which
    is the difference between a spine rounded into one curl and one that
    reverses into an S — two shapes with the same angles at every joint.

    Data, not a verdict: which axis a pose is entitled to turn about is the
    package's business, so the caller reads the component it cares about. A
    curl in the sagittal plane turns about the left-right axis, so one sign of
    x throughout is one unbroken curl:

        spine = [pose["landmarks"][name] for name in skeleton.SPINE]
        sideways = [turn[0] for turn in skeleton.chain_turns(spine)]
        rounded = all(t > 0 for t in sideways) or all(t < 0 for t in sideways)

    Any chain of points does: the spine, an arm, a leg, or a path picked out
    across the figure. A turn of zero length is a straight join rather than a
    fault, so a chain held straight comes back as zero vectors.
    """
    turns = []
    for i in range(len(points) - 2):
        first = [points[i + 1][k] - points[i][k] for k in range(3)]
        second = [points[i + 2][k] - points[i + 1][k] for k in range(3)]
        turns.append([first[1] * second[2] - first[2] * second[1],
                      first[2] * second[0] - first[0] * second[2],
                      first[0] * second[1] - first[1] * second[0]])
    return turns


def axial_rotation(pose,
                   upper=("LEFT_SHOULDER", "RIGHT_SHOULDER"),
                   lower=("LEFT_HIP", "RIGHT_HIP"),
                   axis=("BOTTOM_OF_SACRAL_SPINE", "TOP_OF_THORACIC_SPINE")):
    """Signed degrees `upper` is turned against `lower` about `axis`.

    `angle_at` cannot see a twist: it measures the angle between two bones that
    meet, and a spine that has rotated has not changed a single one of those.
    What a twist is instead is the shoulder girdle and the pelvis pointing in
    different directions around the body's long axis, which is what this
    returns — both girdle axes projected onto the plane perpendicular to the
    spine, and the angle from the lower one to the upper one, positive
    counter-clockwise looking down the axis from the head.

    Data, not a verdict: how much rotation a pose may ask of a spine and two
    hips is the package's judgement, so the caller compares against its own
    band:

        rotation = abs(skeleton.axial_rotation(pose))
        if not 35.0 <= rotation <= 75.0:
            problems.append(f"{rotation:.0f} deg between the girdles")

    Both defaults name a girdle rather than a limb, so passing `upper` and
    `lower` measures any other pair the same way — the two feet against the
    hips in a standing twist, say.
    """
    landmarks = pose["landmarks"]

    def direction(pair):
        a, b = landmarks[pair[0]], landmarks[pair[1]]
        return [b[i] - a[i] for i in range(3)]

    spine = direction(axis)
    length = math.sqrt(sum(c * c for c in spine))
    if length == 0:
        raise ValueError("cannot take a rotation about an axis of zero length")
    spine = [c / length for c in spine]

    def flatten(pair):
        v = direction(pair)
        along = sum(v[i] * spine[i] for i in range(3))
        flat = [v[i] - along * spine[i] for i in range(3)]
        size = math.sqrt(sum(c * c for c in flat))
        if size == 0:
            raise ValueError("cannot take a rotation of an axis parallel to the spine")
        return [c / size for c in flat]

    lo, up = flatten(lower), flatten(upper)
    cross = [lo[1] * up[2] - lo[2] * up[1],
             lo[2] * up[0] - lo[0] * up[2],
             lo[0] * up[1] - lo[1] * up[0]]
    sine = sum(cross[i] * spine[i] for i in range(3))
    cosine = sum(lo[i] * up[i] for i in range(3))
    return math.degrees(math.atan2(sine, cosine))


def point_to_bone(point, a, b):
    """Distance from a landmark to the bone drawn between `a` and `b`.

    The point-to-segment case `check_bone_clearance` does not cover: how near a
    landmark comes to a bone, rather than how near two bones come to each
    other. Poses are described this way — the head reaching the shin, the hand
    finding the foot — so this measures the thing the instruction names.
    """
    d = [b[i] - a[i] for i in range(3)]
    dd = sum(component * component for component in d)
    if dd == 0:
        return distance(point, a)
    t = sum((point[i] - a[i]) * d[i] for i in range(3)) / dd
    t = max(0.0, min(1.0, t))
    return distance(point, [a[i] + d[i] * t for i in range(3)])


def bone_gap(pose, first, second, samples=48):
    """Closest approach between two named bones, in meters.

    Between `point_to_bone`, which measures one landmark against one bone, and
    `check_bone_clearance`, which returns verdicts for the whole figure at one
    or two thresholds. This is the number for a single pair, which is what a
    pose needs when different parts of it are held to different distances: a
    twist may press an arm against a knee at three centimetres while insisting
    the ribcage stay a hand clear of the same thigh, and one figure-wide
    clearance cannot say both.

    Each bone is a (parent, child) pair of landmark names, in either order.
    Both segments are walked and measured against each other, so the result
    converges from above and is exact to well under a millimetre at the default
    sampling — finer than a pose is placed. Raise `samples` for more.

    Data, not a verdict: how close is too close is the pose's own business.

        gap = skeleton.bone_gap(pose, ("LEFT_ELBOW", "LEFT_WRIST"),
                                ("RIGHT_HIP", "RIGHT_KNEE"))
        if gap < 0.03:
            problems.append(f"the forearm passes through the thigh")
    """
    landmarks = pose["landmarks"]
    a, b = landmarks[first[0]], landmarks[first[1]]
    c, d = landmarks[second[0]], landmarks[second[1]]

    def walk(start, end, other_a, other_b):
        return min(
            point_to_bone([start[i] + (end[i] - start[i]) * step / samples
                           for i in range(3)], other_a, other_b)
            for step in range(samples + 1))

    return min(walk(a, b, c, d), walk(c, d, a, b))


def bone_contact(pose, first, second, samples=192):
    """Where two bones come nearest: the gap, and how far along each that is.

    `bone_gap` answers how close two bones pass; this answers where, which is
    the half a pose needs when limbs are meant to be touching. A thigh resting
    on a triceps and a knee grazing a shoulder measure the same distance, and
    only the position along each bone tells them apart — a contact at a
    fraction of 0.98 is on the joint at the end, which is a different pose from
    the one that was asked for.

    Returns `(gap in meters, fraction along first, fraction along second)`,
    each fraction 0 at the bone's first-named landmark and 1 at its second.
    Each bone is a (parent, child) pair of landmark names, in either order —
    but the order chosen is the order the fractions are reported in, so name
    them the way the pose thinks of them.

        gap, along_thigh, along_arm = skeleton.bone_contact(
            pose, ("RIGHT_HIP", "RIGHT_KNEE"), ("RIGHT_SHOULDER", "RIGHT_ELBOW"))
        if along_arm > 0.9:
            problems.append("the leg is resting on the elbow, not the triceps")

    One segment is walked and the other solved exactly at each step, in both
    directions, so the gap agrees with `bone_gap` to well under a millimetre at
    the default sampling. Data, not a verdict.
    """
    landmarks = pose["landmarks"]
    a, b = landmarks[first[0]], landmarks[first[1]]
    c, d = landmarks[second[0]], landmarks[second[1]]

    def walk(start, end, other_start, other_end, swapped):
        edge = [other_end[i] - other_start[i] for i in range(3)]
        span = sum(component * component for component in edge)
        best = (float("inf"), 0.0, 0.0)
        for step in range(samples + 1):
            here = step / samples
            point = [start[i] + (end[i] - start[i]) * here for i in range(3)]
            there = 0.0 if span == 0 else max(0.0, min(1.0, sum(
                (point[i] - other_start[i]) * edge[i]
                for i in range(3)) / span))
            gap = distance(point, [other_start[i] + edge[i] * there
                                   for i in range(3)])
            if gap < best[0]:
                best = (gap, there, here) if swapped else (gap, here, there)
        return best

    return min(walk(a, b, c, d, False), walk(c, d, a, b, True))


def center_of_mass(pose, segments=SEGMENT_MASSES):
    """Where the figure's weight acts, as an [x, y, z] point.

    Each segment's midpoint weighted by its share of body weight. This is what
    says whether a pose is one a body could actually hold: the other checks
    confirm the figure is well-formed and not passing through itself, but only
    this one catches a balance that would topple.

    Data, not a verdict — where the weight may fall is the pose's own business,
    so the caller compares the point against whatever is holding the figure up.
    A standing pose wants it over the feet, an arm balance over the hands:

        com = skeleton.center_of_mass(pose)
        hands = [pose["landmarks"][name] for name in skeleton.FEET_AND_HANDS[2:]]
        over_the_hands = min(h[2] for h in hands) <= com[2] <= max(h[2] for h in hands)

    A pose held deliberately off balance, mid-transition, is free to fail that
    comparison, which is why the judgement is not made here.
    """
    landmarks = pose["landmarks"]
    total = sum(mass for mass, _, _ in segments)
    return [sum(mass * (landmarks[a][axis] + landmarks[b][axis]) / 2
                for mass, a, b in segments) / total
            for axis in range(3)]


def sole_footprint(ankle, toe, heel_behind=0.07, half_width=0.045,
                   toe_half_width=0.036):
    """The corners of one foot's sole on the floor, ready for `base_of_support`.

    A base of support needs three contacts that differ, and a foot flat on the
    ground only offers two landmarks: the ankle and the central toe tip. So
    every one-legged balance — tree, warrior III, half moon — has a base of
    support that `base_of_support` cannot be given. This supplies the missing
    area: the sole is a quadrilateral running from behind the ankle to the toe
    tip, and its four corners are contacts a hull can be taken of.

    `ankle` and `toe` are the two landmarks; only their `x` and `z` matter,
    since an ankle joint centre sits above the floor and the sole does not.
    The three lengths are a foot's shape, defaulting to an adult's: the heel
    reaches `heel_behind` meters back along the foot's own heading, and the
    sole is `half_width` either side at the heel, narrowing to
    `toe_half_width` at the toes. A different figure passes its own.

        corners = skeleton.sole_footprint(landmarks["LEFT_ANKLE"],
                                          landmarks["LEFT_CENTRAL_TOETIP"])
        balanced = skeleton.over_base_of_support(com_on_the_floor, corners)

    Corners come back as [x, y, z] points at y=0, in order around the sole.
    Raises if the ankle and the toe tip coincide seen from above, which gives
    the foot no heading to lay a sole along.
    """
    heading = [toe[0] - ankle[0], 0.0, toe[2] - ankle[2]]
    length = math.hypot(heading[0], heading[2])
    if length == 0:
        raise ValueError("the ankle and the toe tip are in the same place seen "
                         "from above, so the foot has no heading")
    heading = [heading[0] / length, 0.0, heading[2] / length]
    across = [heading[2], 0.0, -heading[0]]

    heel = [ankle[0] - heading[0] * heel_behind, 0.0,
            ankle[2] - heading[2] * heel_behind]
    front = [toe[0], 0.0, toe[2]]
    return [[heel[i] - across[i] * half_width for i in range(3)],
            [heel[i] + across[i] * half_width for i in range(3)],
            [front[i] + across[i] * toe_half_width for i in range(3)],
            [front[i] - across[i] * toe_half_width for i in range(3)]]


def toe_footprint(ankle, toe, ball_behind=0.075, half_width=0.042,
                  toe_half_width=0.036):
    """The corners of the ball-and-toes patch a lifted-heel foot stands on.

    What `sole_footprint` is for a foot flat on the ground, this is for one up on
    its toes. A raised heel carries no weight, so laying a whole sole from behind
    the ankle gives a tiptoe pose a base of support it does not have — and a
    generous one, reaching back under a heel that is in the air. Every heels-up
    balance has the same problem: toe stand, a rise onto the toes, a heels-up
    squat.

    The patch runs from the ball of the foot — the metatarsal heads,
    `ball_behind` meters back along the foot's own heading from the toe tip — to
    the toe tip itself, `half_width` either side at the ball and narrowing to
    `toe_half_width` at the toes. The lengths are a foot's shape, defaulting to
    an adult's; a different figure passes its own.

        patch = skeleton.toe_footprint(landmarks["RIGHT_ANKLE"],
                                       landmarks["RIGHT_CENTRAL_TOETIP"])
        balanced = skeleton.over_base_of_support(
            skeleton.center_of_mass(pose), patch)

    `ankle` and `toe` are the two landmarks and only their `x` and `z` matter,
    since the patch is on the floor whatever height the ankle has been lifted to.
    Corners come back as [x, y, z] points at y=0, in order around the patch.
    Raises if the ankle and the toe tip coincide seen from above, which leaves
    the foot no heading to lay the patch along.
    """
    heading = [toe[0] - ankle[0], 0.0, toe[2] - ankle[2]]
    length = math.hypot(heading[0], heading[2])
    if length == 0:
        raise ValueError("the ankle and the toe tip are in the same place seen "
                         "from above, so the foot has no heading")
    heading = [heading[0] / length, 0.0, heading[2] / length]
    across = [heading[2], 0.0, -heading[0]]

    ball = [toe[0] - heading[0] * ball_behind, 0.0,
            toe[2] - heading[2] * ball_behind]
    front = [toe[0], 0.0, toe[2]]
    return [[ball[i] - across[i] * half_width for i in range(3)],
            [ball[i] + across[i] * half_width for i in range(3)],
            [front[i] + across[i] * toe_half_width for i in range(3)],
            [front[i] - across[i] * toe_half_width for i in range(3)]]


def palm_footprint(wrist, fingertip, heel_behind=0.025, half_width=0.045,
                   finger_half_width=0.038):
    """The corners of one flat hand on the floor, ready for `base_of_support`.

    What `sole_footprint` is for a foot, this is for a hand planted palm-down.
    Every arm balance has the same problem the standing poses do: the two
    landmarks naming the hand — the wrist and the central fingertip — enclose no
    area, so a base of support taken from them alone is a line, and a figure
    balancing on its hands has nothing `base_of_support` can be given. Crow,
    firefly, handstand, plank, wheel, and any seated lift onto the palms.

    The patch runs from the heel of the palm, `heel_behind` meters back from the
    wrist along the hand's own heading, to the fingertips, `half_width` either
    side at the palm and narrowing to `finger_half_width` across the fingers.
    The lengths are a hand's shape, defaulting to an adult's; a different figure
    passes its own.

        patch = skeleton.palm_footprint(landmarks["LEFT_WRIST"],
                                        landmarks["LEFT_CENTRAL_FINGERTIP"])
        held = skeleton.over_base_of_support(
            skeleton.center_of_mass(pose), patch + other_patch)

    `wrist` and `fingertip` are the two landmarks and only their `x` and `z` are
    read, since a wrist joint centre sits above the floor and the palm under it
    does not. Corners come back as [x, y, z] points at y=0, in order around the
    hand. Raises if the wrist and the fingertip coincide seen from above, which
    leaves the hand no heading to lay a palm along.
    """
    heading = [fingertip[0] - wrist[0], 0.0, fingertip[2] - wrist[2]]
    length = math.hypot(heading[0], heading[2])
    if length == 0:
        raise ValueError("the wrist and the fingertip are in the same place seen "
                         "from above, so the hand has no heading")
    heading = [heading[0] / length, 0.0, heading[2] / length]
    across = [heading[2], 0.0, -heading[0]]

    back = [wrist[0] - heading[0] * heel_behind, 0.0,
            wrist[2] - heading[2] * heel_behind]
    front = [fingertip[0], 0.0, fingertip[2]]
    return [[back[i] - across[i] * half_width for i in range(3)],
            [back[i] + across[i] * half_width for i in range(3)],
            [front[i] + across[i] * finger_half_width for i in range(3)],
            [front[i] - across[i] * finger_half_width for i in range(3)]]


def sit_bones(left_hip, right_hip, superior=(0.0, 1.0, 0.0), drop=0.085,
              back=0.035, half_width=0.06):
    """Where a seated figure's two sit bones touch, ready for `base_of_support`.

    What `sole_footprint` is for a standing pose, this is for a seated one. A
    figure on the floor rests on its ischial tuberosities, and no landmark
    names them: `LEFT_HIP` and `RIGHT_HIP` are joint centres up inside the
    pelvis, so they never reach y=0 however correctly the figure sits. Checking
    the hips against the floor therefore fails every seated pose, and leaving
    the seat out of the contacts leaves the pose apparently balanced on its
    legs alone.

    The tuberosities hang `drop` meters below the hip joint centres, `back`
    behind them and `half_width` either side of the midline, carried rigidly
    with the pelvis; the defaults are an adult's and a different figure passes
    its own. `superior` is where the pelvis points, so a pose that tips back or
    rolls onto one side passes its own and the contacts follow — the
    side-to-side axis comes from the two hips themselves, and needs nothing
    said about it.

        seat = skeleton.sit_bones(landmarks["LEFT_HIP"], landmarks["RIGHT_HIP"])
        lifted = [bone for bone in seat if abs(bone[1]) > 0.001]
        held = skeleton.over_base_of_support(
            skeleton.center_of_mass(pose), seat + [landmarks["RIGHT_ANKLE"]])

    Returns [left, right] as [x, y, z] points. Raises if the hips coincide, or
    if `superior` runs along the line between them, since neither leaves the
    pelvis an orientation to hang the sit bones from.
    """
    right = [right_hip[i] - left_hip[i] for i in range(3)]
    width = math.sqrt(sum(c * c for c in right))
    if width == 0:
        raise ValueError("the two hips are in the same place, so the pelvis "
                         "has no width to place sit bones either side of")
    right = [c / width for c in right]

    posterior = [superior[1] * right[2] - superior[2] * right[1],
                 superior[2] * right[0] - superior[0] * right[2],
                 superior[0] * right[1] - superior[1] * right[0]]
    length = math.sqrt(sum(c * c for c in posterior))
    if length == 0:
        raise ValueError("superior runs along the line between the hips, so "
                         "the pelvis has no front and back")
    posterior = [c / length for c in posterior]
    # Re-squared against the hips, so a `superior` that is not quite
    # perpendicular to them still drops the sit bones straight down the pelvis.
    inferior = [posterior[1] * right[2] - posterior[2] * right[1],
                posterior[2] * right[0] - posterior[0] * right[2],
                posterior[0] * right[1] - posterior[1] * right[0]]

    middle = [(left_hip[i] + right_hip[i]) / 2 for i in range(3)]
    return [[middle[i] + inferior[i] * drop + posterior[i] * back
             + right[i] * side * half_width for i in range(3)]
            for side in (-1.0, 1.0)]


def heel(ankle, toe, back=0.045, deep=0.045):
    """Where one foot's heel is, which no landmark names, from its ankle and toe.

    `sit_bones` exists because a seated figure rests on tuberosities the landmark
    list leaves out; the heel is the same omission at the other end. `LEFT_ANKLE`
    is a joint centre up inside the foot, so a pose that grounds its heels, sits
    back on them, or brings a buttock down onto one has nothing to measure
    against — and checking the ankle instead is wrong by the depth of the foot.

    The point returned is the underside of the calcaneus: `back` meters behind
    the ankle along the foot's own axis and `deep` out through its sole, so it
    follows the foot wherever the ankle is pointed, whether the sole is flat, up
    on the toes, or turned over. The defaults are an adult's and a different
    figure passes its own.

        heel = skeleton.heel(landmarks["RIGHT_ANKLE"],
                             landmarks["RIGHT_CENTRAL_TOETIP"])
        grounded = abs(heel[1]) < 0.001

    Returns an [x, y, z] point. Raises if the ankle and the toe tip coincide,
    which leaves the foot no axis to measure back along.
    """
    along = [toe[i] - ankle[i] for i in range(3)]
    length = math.sqrt(sum(c * c for c in along))
    if length == 0:
        raise ValueError("the ankle and the toe tip are in the same place, so "
                         "the foot has no axis to put a heel behind")
    along = [c / length for c in along]

    # The foot's own side-to-side axis: square to its length and to straight up.
    across = [-along[2], 0.0, along[0]]
    width = math.sqrt(sum(c * c for c in across))
    if width == 0:
        raise ValueError("the foot points straight up or straight down, so it "
                         "has no sole to put a heel under")
    across = [c / width for c in across]

    # Out through the sole: square to the foot's axis and to its width, taken
    # downwards, since a sole faces away from the leg however the foot is turned.
    plantar = [along[1] * across[2] - along[2] * across[1],
               along[2] * across[0] - along[0] * across[2],
               along[0] * across[1] - along[1] * across[0]]
    if plantar[1] > 0:
        plantar = [-c for c in plantar]
    return [ankle[i] - along[i] * back + plantar[i] * deep for i in range(3)]


def base_of_support(contacts):
    """The footprint of some ground contacts: their convex hull as (x, z) corners.

    Seen from above, whatever a pose stands on. `contacts` is the landmarks
    carrying the figure's weight — two toetips and two ankles for a standing
    pose with both heels down, the hands as well once one of them reaches the
    floor. Points inside the hull and repeated points are dropped, so passing
    every contact a pose has is fine.

    Corners come back counter-clockwise. Centre lines only: a landmark is a
    joint or a toe tip, not the width of the foot around it, so the polygon is
    the narrow reading of the base and a pose that clears it clears the real
    one. Raises if the contacts do not span an area.
    """
    points = sorted({(c[0], c[2]) for c in contacts})
    if len(points) < 3:
        raise ValueError("a base of support needs three contacts that differ")

    def turn(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        raise ValueError("the contacts are in a line, so they enclose no area")
    return hull


def over_base_of_support(point, contacts):
    """Does `point` fall within the footprint of `contacts`, seen from above?

    What `center_of_mass` is for: a figure whose weight falls outside what is
    holding it up topples, however well-formed the rest of it is.

        com = skeleton.center_of_mass(pose)
        grounded = ("LEFT_CENTRAL_TOETIP", "RIGHT_CENTRAL_TOETIP",
                    "LEFT_ANKLE", "RIGHT_ANKLE")
        held = skeleton.over_base_of_support(
            com, [pose["landmarks"][n] for n in grounded])

    Only `x` and `z` are read, of both the point and the contacts, so the
    caller chooses what is bearing weight rather than this deciding. A pose
    caught mid-transition, or one deliberately tipping, is entitled to answer
    False — whether that is a fault is the package's call, not this one's.
    """
    hull = base_of_support(contacts)
    here = (point[0], point[2])

    def turn(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    return all(turn(hull[i], hull[(i + 1) % len(hull)], here) >= 0
               for i in range(len(hull)))


def support_margin(point, contacts):
    """How far inside the footprint of `contacts` a point falls, in meters.

    `over_base_of_support` answers yes or no, and yes covers both a figure
    squarely over its feet and one six millimetres from the edge of a heel. The
    second is tipping, and a pose that only asks the boolean cannot see the
    difference — which is how a balance passes every check and still looks like
    it is falling over.

    Signed: positive inside the footprint, negative outside, and the magnitude
    is the distance to the nearest edge either way. About three centimetres is
    what a balance held still actually has.

        margin = skeleton.support_margin(skeleton.center_of_mass(pose), palms)
        if margin < 0.03:
            problems.append(f"only {margin * 100:.1f} cm inside the hands")

    Only `x` and `z` are read, as with `over_base_of_support`, so the caller
    decides what is bearing weight. Data, not a verdict: a pose caught
    mid-transition is entitled to a negative margin.
    """
    hull = base_of_support(contacts)
    here = (point[0], point[2])

    nearest = float("inf")
    for i in range(len(hull)):
        ax, az = hull[i]
        bx, bz = hull[(i + 1) % len(hull)]
        ex, ez = bx - ax, bz - az
        span = ex * ex + ez * ez
        along = 0.0 if span == 0 else max(0.0, min(1.0, (
            (here[0] - ax) * ex + (here[1] - az) * ez) / span))
        nearest = min(nearest, math.hypot(here[0] - (ax + ex * along),
                                          here[1] - (az + ez * along)))

    return nearest if over_base_of_support(point, contacts) else -nearest


def validate_landmarks(pose, stem=None):
    """All 20 landmarks present and numeric; optionally `pose` matches `stem`.

    `stem` is the pose file's name without its extension. Pass it and the
    `pose` field is checked against it, which is what the viewer looks up.
    """
    problems = []

    if stem is not None and pose.get("pose") != stem:
        problems.append(
            f"'pose' is {pose.get('pose')!r} but the filename stem is {stem!r}")
    if not pose.get("display_name"):
        problems.append("'display_name' is missing or empty")

    landmarks = pose.get("landmarks")
    if not isinstance(landmarks, dict):
        problems.append("'landmarks' is missing or is not an object")
        return problems

    missing = [name for name in LANDMARKS if name not in landmarks]
    if missing:
        problems.append(f"missing landmarks: {', '.join(missing)}")
    extra = [name for name in landmarks if name not in LANDMARKS]
    if extra:
        problems.append(f"unknown landmarks: {', '.join(extra)}")

    for name, value in landmarks.items():
        if not isinstance(value, list) or len(value) != 3:
            problems.append(f"{name} is not a 3-element list: {value!r}")
            continue
        for axis, component in zip("xyz", value):
            if isinstance(component, bool) or not isinstance(component, (int, float)):
                problems.append(f"{name}.{axis} is not a number: {component!r}")

    return problems


def check_limb_symmetry(pose, tolerance=0.001):
    """Left and right limb lengths agree to within `tolerance` meters.

    One body has one set of bone lengths whatever it is doing, so a mismatch
    here is a typo in the coordinates rather than a property of the pose.
    """
    landmarks = pose["landmarks"]
    problems = []

    for parent, child in LIMB_BONES:
        try:
            left = distance(landmarks[f"LEFT_{parent}"], landmarks[f"LEFT_{child}"])
            right = distance(landmarks[f"RIGHT_{parent}"], landmarks[f"RIGHT_{child}"])
        except KeyError as exc:
            problems.append(f"cannot measure {parent}->{child}: {exc} is missing")
            continue
        if abs(left - right) > tolerance:
            problems.append(
                f"{parent}->{child} differs L/R by {abs(left - right) * 1000:.1f} mm "
                f"(left {left:.3f} m, right {right:.3f} m)")

    return problems


def check_floor_contact(pose, contacts=FEET, clearance=0.05, tolerance=1e-6):
    """The named landmarks rest on the floor and nothing else reaches it.

    `contacts` is the landmarks the pose puts on the ground. Every one must sit
    at y=0 (within `tolerance`), no landmark may dip below it, and everything
    not named must clear it by at least `clearance` meters.
    """
    landmarks = pose["landmarks"]
    problems = []
    contacts = set(contacts)

    unknown = contacts - set(landmarks)
    if unknown:
        problems.append(f"contact landmarks not in the pose: {', '.join(sorted(unknown))}")

    for name, (_, y, _) in landmarks.items():
        if y < -tolerance:
            problems.append(f"{name} is below the floor at y={y:.3f} m")
        elif name in contacts:
            if abs(y) > tolerance:
                problems.append(
                    f"{name} should rest on the floor but is at y={y:.3f} m")
        elif y < clearance:
            problems.append(
                f"{name} is at y={y:.3f} m, under the {clearance:.3f} m clearance, "
                f"but is not listed as touching the floor")

    return problems


def check_resting_heights(pose, ceilings, floor_tolerance=1e-6):
    """No landmark below the floor, and each named one no higher than its ceiling.

    The grounded case `check_floor_contact` cannot express. That check wants its
    contacts at exactly y=0, which is what a toetip or a fingertip does: the
    landmark is at the skin. A reclining pose grounds the sacrum, the ribs, the
    back of the skull, a whole forearm — and those landmarks are joint centres
    *inside* the body, so they rest a few centimetres up and never reach zero.
    Checking them against 0 fails a correct pose; leaving them out of `contacts`
    fails them for being under the clearance.

    So the question a pose on its back asks is not "is this landmark at zero" but
    "is it no higher than its flesh allows, and is nothing below the floor".
    `ceilings` is {landmark: the highest y in meters that landmark may sit at},
    which lets a pose say that the sacrum is within a hand's depth of the ground
    and a dropped knee within a few centimetres of it. Landmarks left out are
    only checked for being above the floor.

    Returns a list of problems, empty when the figure is lying where it should.
    """
    landmarks = pose["landmarks"]
    problems = []

    unknown = set(ceilings) - set(landmarks)
    if unknown:
        problems.append(
            f"ceilings name landmarks not in the pose: {', '.join(sorted(unknown))}")

    for name, (_, y, _) in landmarks.items():
        if y < -floor_tolerance:
            problems.append(f"{name} is below the floor at y={y:.3f} m")
        elif name in ceilings and y > ceilings[name]:
            problems.append(
                f"{name} is at y={y:.3f} m, above the {ceilings[name]:.3f} m its "
                f"contact with the floor allows")

    return problems


def bones():
    """Every bone the viewer draws, as (parent, child) landmark names."""
    drawn = [(SPINE[i + 1], SPINE[i]) for i in range(len(SPINE) - 1)]
    for side in ("LEFT", "RIGHT"):
        drawn.append(("TOP_OF_THORACIC_SPINE", f"{side}_SHOULDER"))
        drawn.append(("BOTTOM_OF_SACRAL_SPINE", f"{side}_HIP"))
        drawn += [(f"{side}_{parent}", f"{side}_{child}")
                  for parent, child in LIMB_BONES]
    return drawn


def _segment_gap(a, b, c, d, steps=200):
    """Smallest distance between segment a-b and segment c-d.

    Sampled along the first segment rather than solved: the closed form has
    several degenerate cases, and a pose only needs to know whether two bones
    come near each other, not the distance to the micron.
    """
    e = [d[i] - c[i] for i in range(3)]
    ee = sum(component * component for component in e)
    best = float("inf")
    for step in range(steps + 1):
        t = step / steps
        point = [a[i] + (b[i] - a[i]) * t for i in range(3)]
        if ee == 0:
            closest = c
        else:
            s = sum((point[i] - c[i]) * e[i] for i in range(3)) / ee
            s = max(0.0, min(1.0, s))
            closest = [c[i] + e[i] * s for i in range(3)]
        best = min(best, distance(point, closest))
    return best


def _bone_pair(first, second):
    """A bone pair as a key that does not care which bone was named first."""
    return frozenset((frozenset(first), frozenset(second)))


def check_bone_clearance(pose, limit=0.05, touching=(), touching_limit=0.03):
    """No two bones that do not share a joint come within `limit` meters.

    A pose can pass every other check and still draw an arm through a thigh:
    the landmarks are all present, the limbs are symmetric and the feet are on
    the floor, but the figure intersects itself. That shows up the moment
    anyone folds, twists or binds, which is most of the interesting poses.

    `limit` is a clearance, not a collision distance — real limbs have width,
    so bones that merely touch at zero already overlap in the flesh.

    A bind presses limbs together on purpose, and lowering `limit` for the
    whole figure to allow it stops the check catching a genuine intersection
    anywhere else. Name those bones instead: `touching` is the bone pairs the
    pose holds against each other, each pair written as two (parent, child)
    tuples in either order, and only they are held to `touching_limit` rather
    than to `limit`.

        skeleton.check_bone_clearance(pose, touching=[
            (("LEFT_WRIST", "LEFT_CENTRAL_FINGERTIP"),
             ("RIGHT_WRIST", "RIGHT_CENTRAL_FINGERTIP"))])

    `touching_limit` is what a clasp has and no more: flesh against flesh, not
    one limb drawn through another. A pair named here that is not two bones the
    viewer draws is reported rather than ignored, so a misspelt landmark is a
    failure instead of a silently missing exception.
    """
    landmarks = pose["landmarks"]
    problems = []

    # Every pair the check actually measures: bones that do not share a joint,
    # since ones that do meet by construction.
    measured = [(first, second)
                for first, second in itertools.combinations(bones(), 2)
                if not set(first) & set(second)]

    pressed = set()
    known = {_bone_pair(first, second) for first, second in measured}
    for first, second in touching:
        key = _bone_pair(first, second)
        if key not in known:
            problems.append(
                f"touching names {tuple(first)} against {tuple(second)}, which "
                f"is not two bones that the viewer draws apart from each other")
            continue
        pressed.add(key)

    for first, second in measured:
        try:
            gap = _segment_gap(landmarks[first[0]], landmarks[first[1]],
                               landmarks[second[0]], landmarks[second[1]])
        except KeyError as exc:
            problems.append(f"cannot measure {first} against {second}: "
                            f"{exc} is missing")
            continue
        allowed = (touching_limit if _bone_pair(first, second) in pressed
                   else limit)
        if gap < allowed:
            problems.append(
                f"{first[0]}-{first[1]} passes within {gap * 100:.1f} cm of "
                f"{second[0]}-{second[1]}, under the {allowed * 100:.0f} cm clearance")

    return problems


def check_origin(pose, tolerance=1e-6):
    """The origin is the floor point beneath the LEFT_HIP/RIGHT_HIP midpoint."""
    landmarks = pose["landmarks"]
    try:
        left, right = landmarks["LEFT_HIP"], landmarks["RIGHT_HIP"]
    except KeyError as exc:
        return [f"cannot locate the hip midpoint: {exc} is missing"]

    x = (left[0] + right[0]) / 2
    z = (left[2] + right[2]) / 2
    if abs(x) > tolerance or abs(z) > tolerance:
        return [f"hip midpoint is at x={x:.4f}, z={z:.4f}; it should sit over the "
                f"origin, so shift the whole figure by (-{x:.4f}, 0, -{z:.4f})"]
    return []
