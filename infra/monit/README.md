# Idle shutdown for the self-hosted dex VM

Powers the box off after thirty minutes of idleness, and gives you a one-command
way to bring it back.

**This targets `bools@dex.tailf2ae8.ts.net`, not the EC2 stack in `../`.** That
box is a VMware guest — Ubuntu 26.04, 8 vCPU of a Xeon E5-2697 v2, 7422 MB RAM,
501 GB disk — running Postgres 18 and dex under pm2 natively. No Docker.

Nothing here is deployed. These are three files to read first and install if you
decide to, and the shutdown ships inert (`DRY_RUN=1`) so you can watch what it
would have done before letting it do it.

| File | Goes to | Runs as |
|---|---|---|
| `dex-idle-stop.conf` | `/etc/monit/conf.d/` on dex, mode 0600 | monit |
| `dex-idle-stop` | `/usr/local/sbin/` on dex, mode 0755 | root, via monit |
| `dex-power-on.sh` | an always-on LAN node — **not** dex | you |
| `dex-power-on.bat` | a Windows box on the tailnet | you |

## The threshold, and why it is 800 MB

Measured on 2026-09-16 with everything up and load at 0.00: **705 MB used** of
7422 MB. 800 MB is that floor plus ~95 MB.

The margin is thin, and deliberately so in the safe direction. `for 30 cycles`
means thirty *consecutive* minutes below the line, so a brief spike resets the
counter and the box stays up. The failure mode is a VM that declines to power
off, never one that powers off mid-task.

Worth knowing: 500 MB, the number in the original brief, is below the idle floor
and would never have fired. The rule would have installed cleanly and done
nothing.

## Install

```bash
sudo apt install monit
sudo install -o root -g root -m 0600 dex-idle-stop.conf /etc/monit/conf.d/
sudo install -o root -g root -m 0755 dex-idle-stop /usr/local/sbin/
sudo sed -i 's/^set daemon .*/set daemon 60 with start delay 900/' /etc/monit/monitrc
sudo monit -t && sudo systemctl enable --now monit
```

`set daemon 60` is load-bearing: the config counts 30 *cycles*, which is thirty
minutes only while a cycle is 60 seconds. Change one and change the other.

Watch it for a week, then commit:

```bash
tail -f /var/log/dex-idle-stop.log
echo 'DRY_RUN=0' | sudo tee /etc/default/dex-idle-stop
```

## What actually decides

Memory is the cheap trigger. `dex-idle-stop` makes the decision, and refuses —
with a logged reason — if any of these disagree:

- uptime under 45 minutes (you just started it; you have not queued work yet)
- `/run/dex-hold` exists (`touch` it to pin the box up; tmpfs, so it clears on boot)
- anyone logged in over SSH (interactive sessions only — `ssh dex <command>`
  allocates no TTY and does not register)
- any task `queued`, `running`, `awaiting_input`, or `paused` without `held`
  — dex resumes its own pauses, so those still count as work; one you parked
  deliberately does not
- any event in the last 30 minutes
- **the database is unreachable** — that is not evidence of idleness, it is
  evidence the script cannot tell, so it does nothing

## Getting it back

Nothing inside a powered-off guest can start it. No sshd, no Tailscale node, no
agent. Wake-on-LAN does not help either: a powered-off VMware guest has no NIC
listening, and a magic packet would wake the physical host, which is already on.

The way back up is a call to the hypervisor from a machine that stayed on.
`192.168.2.5` answers on 443 and is not a tailnet node, so it is almost
certainly the ESXi host — confirm with `govc about` before trusting that.

Two things constrain who can make that call, both measured 2026-09-16:

**No node advertises a subnet route.** `192.168.2.0/24` is not carried over the
tailnet by anyone, so `192.168.2.5` is reachable *only* from machines physically
on that LAN. Everything else has to hop through one of them.

**Your Mac and phones are in a different tailnet.** They are `raduemanuel@` and
see five nodes; `dex` is *shared* into that tailnet from `adrian.paradovschi@`.
So `ds-one`, `cpanel` and `cpanel3` are not reachable from the Mac or from
`iphone-14-pro-max` at all. The power-on trigger has to be run from inside
`adrian.paradovschi@`'s tailnet — or those nodes need sharing too.

Where each machine sits:

| Node | Reaches ESXi? | Address | RTT from dex |
|---|---|---|---|
| `ROLINK-VM` (Windows) | yes — on the LAN | 192.168.2.111 | 2 ms |
| `ds-one` (Synology) | yes — on the LAN | 192.168.2.222 | 2 ms |
| `cpanel` / `cpanel3` | yes — on the LAN | 192.168.2.247 / .246 | 2 ms |
| `adi-leno` (Windows) | no — must hop | via DERP `fra` | 58 ms |
| this Mac, iPhones | no — different tailnet | — | — |

All three Linux LAN nodes had port 22 open. `ds-one` is the natural host for
`dex-power-on.sh` — a NAS is already the thing on that LAN that never goes down:

```bash
ssh ds-one dex-power-on      # from anywhere in adrian.paradovschi@'s tailnet
```

`ROLINK-VM` is the natural host for `dex-power-on.bat`. It is another guest on
the same ESXi host, so it can call the hypervisor directly — though note that a
guest cannot start the host it runs on, so if the ESXi box itself is ever off,
this path is gone too.

The `.bat` handles both positions from one file: it probes `192.168.2.5:443`,
calls `govc.exe` directly if it answers, and otherwise falls back to
`ssh.exe` into a LAN node. So the same script works on `ROLINK-VM` and on
`adi-leno` without editing.

## Before you deploy

- **dex does not advertise a subnet route.** `AdvertiseRoutes` is null, so
  192.168.2.0/24 is not reachable through dex — which is correct here, because
  a route that vanishes when the box powers off would take the recovery path
  down with it. Keep the power-on trigger on a node that is not dex.
- **Check what else lives on this VM.** These probes only looked at dex,
  Postgres and pm2. If the guest does anything else, a memory threshold tuned
  to dex's idle floor will power that off too.
- **Idle is not the same as cheap here.** This is your own hardware, so
  powering the guest off frees RAM and CPU on the ESXi host for other guests —
  it does not stop a meter. The saving is real but it is capacity, not dollars.
