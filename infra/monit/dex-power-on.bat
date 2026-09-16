@echo off
setlocal EnableDelayedExpansion
REM ===========================================================================
REM  Power the dex VM back on, from Windows.
REM
REM  Two paths, chosen automatically, because the two Windows machines on this
REM  tailnet sit in different places (measured 2026-09-16 from dex):
REM
REM    ROLINK-VM  192.168.2.111   2ms   on the LAN -- another guest on the same
REM                                     ESXi host. Can call the hypervisor.
REM    adi-leno   via DERP(fra)  58ms   remote. Cannot.
REM
REM  No node on this tailnet advertises 192.168.2.0/24 as a subnet route, so
REM  192.168.2.5 (the ESXi host) is reachable ONLY from machines physically on
REM  that LAN. A remote Windows box therefore cannot power the VM on directly;
REM  it has to ask a LAN-local node to do it. This script detects which case it
REM  is in and takes the matching path, so the same file works on both.
REM
REM  Requires: Tailscale, and either govc.exe (LAN path) or ssh.exe (hop path).
REM  ssh.exe ships with Windows 10 1809+; govc.exe is a single binary from
REM  https://github.com/vmware/govmomi/releases
REM ===========================================================================

REM --------------------------------------------------------------- settings --
set "ESXI_HOST=192.168.2.5"
set "VM_NAME=dex"
set "TS_NAME=dex.tailf2ae8.ts.net"
set "WAIT_S=180"

REM LAN-local tailnet nodes that can reach the hypervisor, tried in order.
REM All three had port 22 open when this was written. Set HOP_USER to an
REM account that exists on them.
set "HOP_HOSTS=ds-one cpanel cpanel3"
set "HOP_USER=admin"
set "HOP_CMD=dex-power-on"

set "TAILSCALE=%ProgramFiles%\Tailscale\tailscale.exe"

REM Credentials for the LAN path. Keep them in a file, not in this script:
REM   %USERPROFILE%\.dex-power-on.env  containing  GOVC_PASSWORD=...
set "ENVFILE=%USERPROFILE%\.dex-power-on.env"
if exist "%ENVFILE%" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%ENVFILE%") do set "%%A=%%B"
)
if not defined GOVC_USERNAME set "GOVC_USERNAME=root"
set "GOVC_URL=https://%ESXI_HOST%/sdk"
set "GOVC_INSECURE=1"

echo [dex-power-on] target VM "%VM_NAME%" on %ESXI_HOST%

REM ------------------------------------------------- can we reach the ESXi? --
REM One TCP probe decides the path. A 2 second timeout so the remote case does
REM not sit here waiting for a host it can never reach.
powershell -NoProfile -Command ^
  "$c=New-Object Net.Sockets.TcpClient; try{$r=$c.BeginConnect('%ESXI_HOST%',443,$null,$null); if($r.AsyncWaitHandle.WaitOne(2000) -and $c.Connected){exit 0}else{exit 1}}catch{exit 1}finally{$c.Close()}"
if errorlevel 1 goto :hop

REM ------------------------------------------------------------- LAN path ----
echo [dex-power-on] %ESXI_HOST% reachable - calling the hypervisor directly
where govc.exe >nul 2>&1
if errorlevel 1 (
  echo [dex-power-on] ERROR: govc.exe not on PATH.
  echo                Get it from https://github.com/vmware/govmomi/releases
  echo                or run this from a machine that can SSH to a LAN node.
  exit /b 1
)
if not defined GOVC_PASSWORD (
  echo [dex-power-on] ERROR: GOVC_PASSWORD not set. Create %ENVFILE% with:
  echo                GOVC_PASSWORD=your-esxi-password
  exit /b 1
)

REM Already-on is a success, not an error: the point is that dex is up.
for /f "delims=" %%S in ('govc.exe vm.info -json "%VM_NAME%" ^| powershell -NoProfile -Command "$i=$input|ConvertFrom-Json; if($i.virtualMachines){$i.virtualMachines[0].runtime.powerState}else{'NOTFOUND'}"') do set "STATE=%%S"

if "!STATE!"=="NOTFOUND" (
  echo [dex-power-on] ERROR: no VM named "%VM_NAME%" on %ESXI_HOST%
  exit /b 1
)
if "!STATE!"=="poweredOn" (
  echo [dex-power-on] already powered on
  goto :wait
)
echo [dex-power-on] powering on ^(was !STATE!^)
govc.exe vm.power -on "%VM_NAME%"
if errorlevel 1 exit /b 1
goto :wait

REM ------------------------------------------------------------- hop path ----
:hop
echo [dex-power-on] %ESXI_HOST% not reachable from here - hopping via a LAN node
where ssh.exe >nul 2>&1
if errorlevel 1 (
  echo [dex-power-on] ERROR: ssh.exe not found. Enable the OpenSSH Client:
  echo                Settings ^> Apps ^> Optional Features ^> OpenSSH Client
  exit /b 1
)

for %%H in (%HOP_HOSTS%) do (
  echo [dex-power-on] trying %HOP_USER%@%%H
  ssh.exe -o ConnectTimeout=8 -o BatchMode=yes %HOP_USER%@%%H "%HOP_CMD%"
  if not errorlevel 1 goto :wait
  echo [dex-power-on]   %%H did not work, next
)
echo [dex-power-on] ERROR: no LAN node accepted the command.
echo                Check HOP_USER, and that your key is authorised there.
exit /b 1

REM ---------------------------------------------------------------- wait -----
REM Powered on is not the same as usable: the guest still has to boot, start
REM tailscaled and start pm2. Waiting for the tailnet name to answer is the
REM closest thing to "dex is back" visible from outside.
:wait
echo [dex-power-on] waiting up to %WAIT_S%s for %TS_NAME%
set /a "ELAPSED=0"
:waitloop
if not exist "%TAILSCALE%" goto :nowait
"%TAILSCALE%" ping -c 1 --timeout 2s "%TS_NAME%" >nul 2>&1
if not errorlevel 1 (
  echo [dex-power-on] %TS_NAME% is up
  exit /b 0
)
timeout /t 5 /nobreak >nul
set /a "ELAPSED+=5"
if !ELAPSED! lss %WAIT_S% goto :waitloop
echo [dex-power-on] still not answering after %WAIT_S%s - powered on, check the console
exit /b 1

:nowait
echo [dex-power-on] tailscale.exe not found; powered on, not waiting
exit /b 0
