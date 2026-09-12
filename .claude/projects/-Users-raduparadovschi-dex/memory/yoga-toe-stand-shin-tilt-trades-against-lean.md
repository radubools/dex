---
name: yoga-toe-stand-shin-tilt-trades-against-lean
description: In a one-legged deep squat the shin's tilt is the only free parameter, and it trades directly against the trunk's forward lean.
metadata:
  type: project
---

A knee at its limit fixes the hip-to-ankle distance (0.40 thigh + 0.41 shin at a
32° interior angle span 0.24 m), so the standing leg has exactly one free choice
left: how far forward the shin leans. Everything else falls out of it. Putting
the hip *directly above* the ankle — the intuitive reading of "the buttock rests
on the heel" — needs a shin about 70° off vertical, which is a horizontal tibia,
not a squat. A steep shin (40°) is a proper squat but throws the forefoot 0.22 m
in front of the hip, and no trunk lean reaches that far: the centre of mass
saturates around 0.21 m and the balance solve has no bracket.

Toe Stand settled at a 59° shin, which costs 20° of forward trunk lean.

**Why:** the three things a brief asks for here — buttock on the heel, balanced
on the toes, torso upright — are in direct tension, and the tension is
geometric, not a modelling error. Guessing a shin angle produces a figure that
is either unbalanceable or not squatting.

**How to apply:** parametrise the shin tilt, solve the trunk lean for balance at
each value, and print the row — the lean, the hip and knee heights, the hip
flexion — then pick. See [[yoga-hero-pose-heels-set-the-knee-angle]] for the
same shape of argument at the ankle. Sideways, do not lean: adduct until the
foot is under the weight ([[yoga-one-legged-balance-needs-adduction]]), and only
a few degrees of lean are left over.
