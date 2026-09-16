#!/usr/bin/env bash
# Power the dex VM back on, from somewhere that is still switched on.
#
# This does NOT run on dex. Nothing inside a powered-off VMware guest can start
# it -- the guest is not running, so there is no agent, no sshd, and no
# Tailscale node to reach. The way back up is always a call to the hypervisor,
# from a machine that stayed up.
#
# Install on a LAN-local tailnet node that is always on. Measured from dex on
# 2026-09-16, these are on 192.168.2.0/24 and answer over the tailnet directly
# rather than through a DERP relay:
#
#   ds-one    192.168.2.222   2ms   Synology (MAC 00:11:32:..)
#   cpanel    192.168.2.247   2ms
#   cpanel3   192.168.2.246   2ms
#
# ds-one is the natural host: a NAS is already the thing on that LAN that never
# goes down. Then `ssh ds-one dex-power-on` from a phone over Tailscale is the
# whole trigger -- no port forwarding, no public endpoint, no shared secret,
# because Tailscale has already done the authentication.

set -euo pipefail

# ---------------------------------------------------------------- settings ---
# 192.168.2.5 answers on 443 and is not a tailnet node, so it is almost
# certainly the ESXi host. Confirm before trusting it: `govc about` should name
# the product and version.
ESXI_HOST="${ESXI_HOST:-192.168.2.5}"
VM_NAME="${VM_NAME:-dex}"
TS_NAME="${TS_NAME:-dex.tailf2ae8.ts.net}"
WAIT_S="${WAIT_S:-180}"

# govc reads these. Keep the password in a file the trigger user alone can
# read, not in this script and not in the environment of every shell:
#   install -m 0600 /dev/null /etc/dex-power-on.env
#   printf 'GOVC_PASSWORD=...\n' >> /etc/dex-power-on.env
# shellcheck source=/dev/null
[[ -r /etc/dex-power-on.env ]] && source /etc/dex-power-on.env
export GOVC_URL="${GOVC_URL:-https://${ESXI_HOST}/sdk}"
export GOVC_USERNAME="${GOVC_USERNAME:-root}"
export GOVC_INSECURE="${GOVC_INSECURE:-1}"   # ESXi ships a self-signed cert

say() { printf '%s %s\n' "$(date -Is)" "$*"; }

# ------------------------------------------------------------------ power ----
if ! command -v govc >/dev/null; then
    cat >&2 <<'HINT'
govc is not installed. It is a single static binary:
  https://github.com/vmware/govmomi/releases  (govc_Linux_x86_64.tar.gz)

Or, without govc at all, if the ESXi host has SSH enabled:
  ssh root@192.168.2.5 'vim-cmd vmsvc/getallvms'            # find the vmid
  ssh root@192.168.2.5 'vim-cmd vmsvc/power.on <vmid>'
HINT
    exit 1
fi

state=$(govc vm.info -json "$VM_NAME" | python3 -c \
    'import sys,json; v=json.load(sys.stdin)["virtualMachines"]; print(v[0]["runtime"]["powerState"] if v else "NOTFOUND")')

case "$state" in
    NOTFOUND)    say "no VM named '$VM_NAME' on $ESXI_HOST"; exit 1 ;;
    poweredOn)   say "'$VM_NAME' is already powered on" ;;
    *)           say "powering on '$VM_NAME' (was $state)"
                 govc vm.power -on "$VM_NAME" ;;
esac

# ------------------------------------------------------------------- wait ----
# Powered on is not the same as usable: the guest still has to boot, bring up
# tailscaled, and start pm2. Waiting for the tailnet name to answer is the
# closest thing to "dex is back" that this script can see from outside.
say "waiting up to ${WAIT_S}s for $TS_NAME"
deadline=$(( $(date +%s) + WAIT_S ))
while (( $(date +%s) < deadline )); do
    if tailscale ping -c 1 --timeout 2s "$TS_NAME" >/dev/null 2>&1; then
        say "$TS_NAME is up"
        exit 0
    fi
    sleep 5
done
say "still not answering after ${WAIT_S}s -- powered on, but check the console"
exit 1
