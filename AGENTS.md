# AGENTS.md — working in this repo as an AI agent

Read this whole file before changing anything. **This repo drives a
health-safety device**: a wattage-based failure alarm for a CPAP machine.
The failure mode that matters is not "crash" — it is *silently not
alarming while someone sleeps*.

## Prime safety rules

1. **Never leave the device unable to alarm.** If you touch the running
   Pi, confirm the service is active and ARMED before you disconnect:
   `systemctl status cpap-monitor` + a fresh heartbeat file + an
   `ARMED` line in `journalctl -u cpap-monitor`.
2. **Secrets never enter git.** This repo is PUBLIC. No plug/vendor
   account credentials, no Wi-Fi passphrases, no Pushover tokens, no
   real emails — only `REPLACE_ME` placeholders in
   `config/config.example.toml`. The real config is
   `/etc/cpap-monitor/config.toml` (0640, root:cnewton, outside the
   repo). Also excluded: CPAP health logs, nightly summaries, mp3s.
   Before any commit: `git diff --staged` and grep for anything that
   looks like a credential.
3. **Test before trusting.** After any change: `pytest tests/`, then on
   the Pi `python3 -m cpapmon validate`, `check` (plug answers, watts
   sane), `simulate` (logic fires), and ideally `test-alert` (siren is
   audible). Never assume; observe.
4. **Bias conservative.** When editing alarm logic, prefer a false alarm
   over a missed one. The state machine intentionally refuses to silence
   an active alarm on anything but a verified-safe reading.

## Repo structure

```
cpapmon/                  Python package (runs in place; no pip install)
  state.py                THE alarm logic: pure state machine
                          SEARCHING -> ARMED -> ALARMING. No I/O. All
                          behaviour changes start (and are tested) here.
  backends/
    base.py               contract: read_watts() -> float | PlugUnreachable
    tasmota.py            the only shipped backend: local HTTP, any mains voltage
    __init__.py           make_backend(cfg) factory (keeps backends pluggable)
  config.py               tomllib loader + validate(); dataclasses
  runner.py               poll loop; turns state-machine events into
                          siren/log/notify/heartbeat side effects
  sound.py                looping sox `play` siren (primary alarm channel)
  notify.py               Pushover (stub until token set); log fallback;
                          email intentionally dropped
  heartbeat.py            atomic JSON status file (anti-silent-failure)
  __main__.py             CLI: run | check | validate | simulate | test-alert
tests/test_monitor.py     pytest, pure logic, no hardware
config/config.example.toml  the ONLY config in git (placeholders)
systemd/cpap-monitor.service
scripts/install.sh        idempotent: unit + /etc/cpap-monitor + perms
docs/DEPLOY.md            fresh-Pi bring-up, incl. plug Wi-Fi network
docs/DESIGN.md            architecture + anti-silent-failure rationale
docs/TRAVEL.md            new-location/voltage arrival checklist (thresholds are
                          watt-based = voltage-independent; verify meter on-site)
```

## Conventions

* Python >= 3.11 (stdlib `tomllib`), stdlib-only at runtime — the tasmota
  backend uses only urllib. A future backend with third-party deps must
  import them lazily inside its own module so the tasmota path never
  needs them.
* All alarm *decisions* go in `state.py` and get a test. `runner.py` only
  executes events. Don't let I/O leak into `state.py`.
* Thresholds are WATTS (voltage-independent) with field-tuned defaults
  (4.3 / 18.5 / 60 s / 60 s / 180 s). Don't change defaults casually —
  they encode real-hardware tuning against a ResMed (18.5 W exists
  because deep breaths spike the draw).
* Config: `backend = "..."` selects the plug backend. Tasmota is the only
  shipped backend, but keep the key and the factory — the architecture
  stays backend-selectable for a future plug type.
* Logging goes to stdout; journald owns persistence. Never write
  root-owned files into the deploy dir (classic crash-loop trap).

## How to add a backend (none planned; Tasmota is the only one shipped)

Implement `read_watts()` per `backends/base.py`, add a config dataclass +
section in `config.py`, and a branch in `make_backend()` — `state.py`
never changes. After any backend/config edit on the Pi:
`python3 -m cpapmon validate && python3 -m cpapmon check` and
`sudo systemctl restart cpap-monitor`; verify the ARMED log line appears.

## How to deploy to a new Pi

Follow docs/DEPLOY.md exactly; short form: clone to
`/home/cnewton/cpap-monitor`, `pip install -r requirements.txt`,
`sudo scripts/install.sh`, fill `/etc/cpap-monitor/config.toml`, bring up
the plug network, `check`, `test-alert`, arm, verify heartbeat.

## Set up from a more powerful machine (recommended for low-RAM Pis)

The reference hardware is a **Raspberry Pi 3B+ with ~1 GB RAM**. That's plenty
to *run* the monitor (the loop is tiny), but **too memory-constrained to
comfortably run a heavyweight AI coding assistant — e.g. Claude Code — on the Pi
itself** while setting up. Don't bog the Pi down:

* **Do the setup/config work from a separate, more powerful computer** and give
  it **SSH access to the Pi** for the duration — either a **sudo-capable
  account** or a **temporary root key**. All the heavy lifting (editing,
  testing, tuning thresholds) happens off-box; the Pi just executes commands.
* This repo was built exactly that way: the agent ran on a workstation and drove
  the Pi over SSH via a dedicated, clearly-labeled temporary key in the Pi's
  `/root/.ssh/authorized_keys`.
* **Tear the access down when you're finished** — the Pi is long-lived and
  physically accessible, so leave it no more open than you found it:
  1. remove the temporary key line(s) from `/root/.ssh/authorized_keys` (and any
     `~cnewton/.ssh/authorized_keys` you added);
  2. remove any **passwordless-login / passwordless-sudo** drop-in created for
     the agent (e.g. a `NOPASSWD` entry in `/etc/sudoers.d/`);
  3. re-tighten sshd (key-only auth; consider disabling `rpcbind` and password
     auth). See the repo's security-hardening notes.

## Deploying to a Pi that has no internet (the live rig)

The live device is a Pi (hostname `cpap1`) set up and updated from a separate
workstation over a **direct Ethernet crossover** cable — not the plug AP, not the
house LAN. Facts worth not re-discovering:

* **The Pi has no route to the internet / GitHub.** Over the crossover it can
  reach only the workstation. So the git flow is: edit + commit on the
  workstation's clone, `git push` to GitHub *from the workstation*, then deploy to
  the Pi over the crossover (next bullet) — never `git pull` on the Pi.
* **Update the Pi's live checkout without GitHub.** The Pi's working clone at
  `/home/cnewton/cpap-monitor` has `git config receive.denyCurrentBranch
  updateInstead`, so a plain `git push <cnewton@pi>:/home/cnewton/cpap-monitor
  main:main` from the workstation updates the working tree *in place* to the exact
  same commit (keep the Pi's tree clean or the push is refused). Then on the Pi:
  `sudo systemctl restart cpap-monitor` and confirm it returns SEARCHING→ARMED
  with a fresh heartbeat (prime safety rule 1).
* **Crossover down?** NetworkManager has no IPv4LL fallback; the link rides a
  `shared`-mode connection (`crossover-cpap1`) so the workstation runs dnsmasq
  (workstation `10.42.0.1`, Pi leases `10.42.0.136`). If the workstation NIC shows
  `carrier 0` / NM "unavailable" (cable knocked), `nmcli connection up
  crossover-cpap1` and the Pi re-requests DHCP once the link returns.
* Access to the Pi is by the operator's own credentials, held **off** the repo
  (secrets rule 2). This path leaves no standing temp key to tear down, but the
  teardown discipline for any key/NOPASSWD you *do* add still applies.
* **No pytest on the Pi.** `tests/test_snooze.py` runs as a plain script
  (`PYTHONPATH=. python3 tests/test_snooze.py`) precisely so the state-machine
  tests can be verified on the device itself after a deploy.

## Field-discovered gotchas (hard-won; do not rediscover)

* **The Pi hosts its own `cpap` Wi-Fi AP** for the plugs (hostapd/
  NetworkManager AP mode, subnet 192.168.77.0/24). The plugs are NOT on
  the house LAN. If `check` says unreachable, verify the AP is up before
  blaming the plug.
* **ESP8266 Tasmota needs WPA2-AES with PMF (802.11w) DISABLED** or it
  will not associate with the AP. If a Tasmota plug joins-then-drops or
  never gets an IP, check the AP's PMF/WPA3 settings first.
* **Phones will not hold a no-internet setup AP.** Android/iOS bounce off
  the plug's setup hotspot because it has no internet. Configure plugs
  from a computer, or via the Pi itself.
* **Corrupted Wi-Fi firmware = no `wlan0` at all.** Seen in the field on
  the Pi: reinstall `firmware-brcm80211` and reboot.
* **Tasmota `Status 10` response shape varies** by build: `ENERGY` may be
  under `StatusSNS`, top-level, or under `SENSOR`.
  `tasmota.extract_power_watts()` handles all three — keep it tolerant.
* **Smart plugs move IPs unless you pin them.** Always give the plug a
  static DHCP reservation (network/setup-network.sh does this) and keep
  `[tasmota] host` matching — IP drift is how the predecessor alarm was
  silently dead for 78 days.

## History that shapes this design

The predecessor alarm was **silently dead for 78 days** (an email-notify
path rotted, and nothing watched the watcher). That is why: startup logs
an explicit ARMED line, a heartbeat file is refreshed every 30 s,
`validate` gates systemd startup, email was dropped for Pushover, and
docs/DESIGN.md carries the external-watchdog roadmap. Anything you build
here must fail LOUDLY.
