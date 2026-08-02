# DEPLOY.md — standing up cpap-monitor on a fresh Raspberry Pi

Goal: from blank Pi to an ARMED, siren-verified monitor. Follow in order;
each step ends with a verification. Nothing here requires the repo to
contain secrets — you fill those in at step 4.

Assumptions: Raspberry Pi OS (Bookworm or later, Python >= 3.11), user
`cnewton`, speakers/amp on the Pi's audio output, one supported smart
plug (Tasmota preferred — see README).

## 0. OS basics

```bash
sudo apt update
sudo apt install -y git python3-pip sox libsox-fmt-mp3
```

Verify audio now, before anything else — a monitor with dead audio is
decoration:

```bash
speaker-test -t sine -f 880 -l 1   # you must HEAR this
```

## 1. Clone the repo

```bash
git clone <this-repo-url> /home/cnewton/cpap-monitor
cd /home/cnewton/cpap-monitor
```

The package runs in place (`python3 -m cpapmon` from the repo root); no
pip install of the project itself.

## 2. Python dependencies

```bash
pip install -r requirements.txt --break-system-packages
```

(`python-kasa` is only exercised by the kasa backend; the tasmota path is
pure stdlib. `--break-system-packages` or a venv — your call; the systemd
unit uses system python3.)

## 3. Bring up the plug network (the Pi-hosted `cpap` AP)

The plugs do NOT live on the house Wi-Fi. The Pi hosts its own
access point, SSID `cpap`, subnet `192.168.77.0/24`, and the plugs join
it. This keeps the alarm loop independent of the house router and makes
the whole rig portable (hotel rooms, Iceland).

With NetworkManager (Bookworm default):

```bash
sudo nmcli con add type wifi ifname wlan0 con-name cpap-ap \
    autoconnect yes ssid cpap mode ap \
    ipv4.method shared ipv4.addresses 192.168.77.1/24 \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk 'REPLACE_ME'
sudo nmcli con up cpap-ap
```

**Hard-won networking notes (read before fighting the plugs):**

* **ESP8266 Tasmota plugs require WPA2-AES with PMF (802.11w) disabled**
  — they will not associate otherwise. If your NM build enables PMF by
  default: `sudo nmcli con modify cpap-ap wifi-sec.pmf 1` (1 = disable).
  No WPA3/SAE-mixed modes on this AP.
* **Don't configure plugs from a phone.** Phones refuse to stay on the
  plug's no-internet setup hotspot. Use a laptop, or configure via the
  Pi itself.
* **No `wlan0` at all?** The Broadcom Wi-Fi firmware can corrupt:
  `sudo apt install --reinstall firmware-brcm80211 && sudo reboot`.
* Give the plugs static DHCP reservations (or static IPs in Tasmota):
  convention is `.10` for the Kasa plug, `.11` for the Tasmota plug.

Verify: plug joins the AP and answers —

```bash
ping -c3 192.168.77.11
curl 'http://192.168.77.11/cm?cmnd=Status%2010'    # JSON with ENERGY.Power
```

## 4. Config (the ONLY place secrets live)

```bash
sudo scripts/install.sh        # creates /etc/cpap-monitor + unit (idempotent)
sudo nano /etc/cpap-monitor/config.toml
```

Replace **every** `REPLACE_ME`:

* `[kasa] username/password` — TP-Link account (only if you'll use kasa)
* `[notify.pushover] token/user_key` — if/when enabling push
* check `[tasmota] host` / `[kasa] host` match your reservations
* `[sound] alarm_file` — copy a LOUD mp3 onto the Pi (mp3s are
  deliberately not in the repo):
  `mkdir -p /home/cnewton/cpap-monitor/sounds && cp <your-alarm>.mp3 /home/cnewton/cpap-monitor/sounds/alarm.mp3`

The file must stay `0640 root:cnewton` (install.sh enforces this).
Never copy a filled config back into the repo.

Verify:

```bash
cd /home/cnewton/cpap-monitor
python3 -m cpapmon validate     # must print OK
```

## 5. Verify the plug through the monitor's own eyes

```bash
python3 -m cpapmon check
# backend: tasmota @ http://192.168.77.11 (timeout 3.0s)
# watts: 11.42  (38 ms)
# band:  normal activity band
```

Run it with the CPAP off (expect the no-breath band) and on with the
mask sealed (expect the activity band). If the numbers hug a threshold,
re-tune `[thresholds]`.

Also dry-run the logic and the alert path:

```bash
python3 -m cpapmon simulate                 # canned die-and-recover scenario
python3 -m cpapmon test-alert               # you must HEAR the siren
```

## 6. Arm

```bash
sudo systemctl restart cpap-monitor
journalctl -u cpap-monitor -f
```

You are looking for the line:

```
ARMED — plug answered (...) ; monitoring is LIVE
```

Then confirm the heartbeat is fresh:

```bash
cat /home/cnewton/cpap-monitor/logs/status.json
```

## 7. Prove it end-to-end (do not skip)

With the system armed and the CPAP running: **pull the CPAP's power**
(from the smart plug's outlet, leaving the plug itself powered). Within
~60 s the siren must fire; plug it back in and the siren must stop.
An alarm you have never heard fire is not an alarm.

## Reboot survivability

`Restart=always` + `WantedBy=multi-user.target` mean the monitor returns
after power loss. Verify once: `sudo reboot`, wait, check `journalctl`
for the startup-delay + SEARCHING + ARMED lines.

## Updating

```bash
cd /home/cnewton/cpap-monitor && git pull
pytest tests/ && sudo scripts/install.sh    # re-runs safely, restarts unit
```
