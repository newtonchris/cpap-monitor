# DEPLOY.md — standing up cpap-monitor on a fresh Raspberry Pi

Goal: from blank Pi to an ARMED, siren-verified monitor. Follow in order;
each step ends with a verification. Nothing here requires the repo to
contain secrets — you fill those in at steps 3 and 5, in gitignored /
outside-the-repo files only.

Assumptions: Raspberry Pi OS (Bookworm or later, Python >= 3.11,
NetworkManager), user `cnewton`, speakers/amp on the Pi's audio output,
one supported smart plug (Tasmota preferred — see README).

The whole sequence, which the rest of this file walks through:

```bash
make deps && make network && make install && make config
sudo $EDITOR /etc/cpap-monitor/config.toml       # fill every REPLACE_ME
make validate && make check && make siren && make test-alert && make arm
```

`make help` lists every target. Targets are thin wrappers over
`scripts/` and the `python3 -m cpapmon` CLI, and work run as root or via
sudo. (Doing the setup from a bigger machine over SSH? See AGENTS.md —
and `make teardown-access` when you're done.)

## 0. OS basics + dependencies

```bash
git clone <this-repo-url> /home/cnewton/cpap-monitor
cd /home/cnewton/cpap-monitor
make deps          # apt: git, pip, sox (+mp3/all formats); pip: requirements.txt
```

The package runs in place (`python3 -m cpapmon` from the repo root); no
pip install of the project itself.

Verify audio now, before anything else — a monitor with dead audio is
decoration:

```bash
speaker-test -t sine -f 880 -l 1   # you must HEAR this
```

## 1. Bring up the plug network (the Pi-hosted `cpap` AP + DHCP)

The plugs do NOT live on the house Wi-Fi. The Pi hosts its own access
point (SSID `cpap`, subnet `192.168.77.0/24`) **and is the DHCP server
for it** — a NetworkManager AP-mode connection (`cpap-ap`) with
`ipv4.method shared`, so NM runs its own dnsmasq. This keeps the alarm
loop independent of the house router and makes the rig portable (hotel
rooms, Iceland). Topology + full details: [../network/README.md](../network/README.md).
(Stationary home box? This step is optional — see that README.)

```bash
cp network/network.env.example network/network.env
$EDITOR network/network.env    # SSID, Wi-Fi passphrase, plug MAC→IP pins
make network                   # idempotent; re-run to apply changes
```

`network.env` is gitignored (it holds the passphrase — public repo).
The script applies the field-proven settings exactly, including the two
that matter most:

* **WPA2-AES with PMF (802.11w) disabled** — ESP8266 Tasmota plugs will
  not associate otherwise. Set explicitly; do not "modernize".
* **Static DHCP reservations pinning each plug's IP by MAC** (installed
  to `/etc/NetworkManager/dnsmasq-shared.d/cpap-plugs.conf`). Unpinned
  plugs drift to new leases and silently break the monitor's `host`
  config — IP drift is how the predecessor alarm was dead for 78 days.
  Convention: `.10` Kasa, `.11` Tasmota.

**Hard-won networking notes (read before fighting the plugs):**

* **Don't configure plugs from a phone.** Phones refuse to stay on the
  plug's no-internet setup hotspot. Use a laptop, or configure via the
  Pi itself.
* **No `wlan0` at all?** The Broadcom Wi-Fi firmware can corrupt:
  `sudo apt install --reinstall firmware-brcm80211 && sudo reboot`.
  (`make network` detects this and stops; it won't reboot your Pi.)

Verify: plug joins the AP, gets its pinned IP, and answers —

```bash
cat /var/lib/NetworkManager/dnsmasq-wlan0.leases   # plug MAC at its pinned IP
ping -c3 192.168.77.11
curl 'http://192.168.77.11/cm?cmnd=Status%2010'    # JSON with ENERGY.Power
```

## 2. Install the service

```bash
make install       # scripts/install.sh: systemd unit + /etc/cpap-monitor, idempotent
```

## 3. Config (the ONLY place app secrets live)

```bash
make config        # seeds /etc/cpap-monitor/config.toml if absent
sudo $EDITOR /etc/cpap-monitor/config.toml
```

Replace **every** `REPLACE_ME`:

* `[kasa] username/password` — TP-Link account (only if you'll use kasa)
* `[notify.pushover] token/user_key` — if/when enabling push
* check `[tasmota] host` / `[kasa] host` match the reservations you
  pinned in step 1 (`.11` / `.10`)
* `[sound] alarm_file` — either keep the shipped escalating siren
  (regenerate with `make siren`) or copy your own LOUD mp3 onto the Pi
  (mp3s are deliberately not in the repo):
  `cp <your-alarm>.mp3 /home/cnewton/cpap-monitor/sounds/alarm.mp3`

The file must stay `0640 root:cnewton` (install.sh enforces this).
Never copy a filled config back into the repo.

Verify:

```bash
make validate      # must print OK
```

## 4. Verify the plug through the monitor's own eyes

```bash
make check
# backend: tasmota @ http://192.168.77.11 (timeout 3.0s)
# watts: 11.42  (38 ms)
# band:  normal activity band
```

Run it with the CPAP off (expect the no-breath band) and on with the
mask sealed (expect the activity band). If the numbers hug a threshold,
re-tune `[thresholds]`.

Also dry-run the logic and the alert path:

```bash
make simulate      # canned die-and-recover scenario through the state machine
make test-alert    # you must HEAR the siren
```

## 5. Arm

```bash
make arm           # restarts the service and shows its status
journalctl -u cpap-monitor -f
```

You are looking for the line:

```
ARMED — plug answered (...) ; monitoring is LIVE
```

Then confirm the heartbeat is fresh:

```bash
make status        # service state + heartbeat age (fails loudly if missing)
```

## 6. Prove it end-to-end (do not skip)

With the system armed and the CPAP running: **pull the CPAP's power**
(from the smart plug's outlet, leaving the plug itself powered). Within
~60 s the siren must fire; plug it back in and the siren must stop.
An alarm you have never heard fire is not an alarm.

## Reboot survivability

`Restart=always` + `WantedBy=multi-user.target` mean the monitor returns
after power loss, and `cpap-ap` autoconnects (priority 100) so the plug
net comes back too. Verify once: `sudo reboot`, wait, `make status`, and
check `journalctl` for the startup-delay + SEARCHING + ARMED lines.

## If an agent/remote machine did the setup: revoke its access

Per AGENTS.md, heavy setup work is best driven from a more powerful
machine over SSH with a temporary key and (sometimes) passwordless sudo.
The Pi is long-lived and physically accessible — remove that access now:

```bash
make teardown-access                          # review what's granted
make teardown-access MARKER=<key-comment> CONFIRM=yes
```

Then re-tighten sshd (key-only auth) and re-run `make status` to confirm
the monitor is still ARMED before you disconnect.

## Updating

```bash
cd /home/cnewton/cpap-monitor && git pull
make test && make install     # install.sh re-runs safely, restarts unit
```
