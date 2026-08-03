# Traveling with the monitor — new-location arrival checklist

The alarm logic is **voltage-independent**: it thresholds on **watts**, and the CPAP
draws the same real power (watts) at 110 V or 220 V — only the *current* changes
(it halves at 220 V; the wattage stays the same). The monitor reads **only power**
and ignores the voltage field, so the thresholds carry across countries unchanged.

**So there is nothing to change in the software for a different voltage.** The only
things to verify on arrival are *physical*: that the plug is rated/calibrated for the
local mains, and that everything powered up and re-armed. This checklist is the
30-second on-site verification — do it **once, awake, before you rely on it to sleep.**

> **Bring the Tasmota plug.** It is dual-voltage (EU/220 V-rated) and the monitor's
> only plug — nothing in the software changes for travel.

---

## Before you leave home
- [ ] Confirm `backend = "tasmota"` in `/etc/cpap-monitor/config.toml`.
- [ ] Pack the **EU/220 V-rated Tasmota** plug (Iceland = **Type F / Schuko, 230 V / 50 Hz**),
      the Raspberry Pi, and a short Ethernet cable for the Pi's uplink.
- [ ] **Write down your home baseline** so you have a reference to compare against:
      run `python3 -m cpapmon --config /etc/cpap-monitor/config.toml check` with the
      CPAP running and note the watts (e.g. **~7–8 W breathing, ~3–4 W idle**).

## On arrival (the actual checklist)
1. **Power up, in order:** Pi (into local Ethernet for internet) → wait ~1 min for the
   Pi's `cpap` Wi-Fi AP to come up → plug in the Tasmota → plug the CPAP into the Tasmota.
2. **Confirm the plug rejoined the `cpap` network:**
   ```
   grep -i tasmota /var/lib/NetworkManager/dnsmasq-wlan0.leases
   ```
   It should show a `192.168.77.x` lease. (Once a plug has joined `cpap` once, it
   auto-rejoins forever — no re-setup needed.) If it did NOT join, see *Gotchas* below.
3. **Verify the meter reads correctly at local voltage** — the key step. With the CPAP
   **running**:
   ```
   python3 -m cpapmon --config /etc/cpap-monitor/config.toml check
   ```
   - ✅ **Watts match your home breathing band (~7–8 W)** → thresholds are perfect, done.
     (The reported *voltage* will read ~230 V — expected and irrelevant; the software
     only uses watts.)
   - ⚠️ **Watts are noticeably off** (e.g. idle no longer clears the 4.3 W line, or
     running sits far from ~7–8 W) → the plug's meter is mis-calibrated for 220 V.
     Fix by either re-tuning the two thresholds in the config to bracket the observed
     idle vs running numbers, or calibrating the plug (Tasmota `PowerSet`/`VoltageSet`).
     Re-run `check` until idle is clearly below and running clearly above
     `activity_watt_limit`.
4. **Arm and confirm it's live:**
   ```
   sudo systemctl restart cpap-monitor     # (or your run method)
   ```
   Watch for the **ARMED** log line / Pushover "armed" message, and check
   `logs/status.json` shows `"state": "ARMED"` with a sane wattage.
5. **One daytime live drill** (recommended, while awake): with it armed and the CPAP
   running, **turn the CPAP off for ~60 s** → confirm the siren fires (and the push, if
   wired) → **turn it back on** → confirm it auto-silences (`ALARM_STOP` / back to
   ARMED). That proves detect → alarm → recover end-to-end at the new voltage.
6. Only after the drill passes: rely on it for sleep. **When you wake and pull the
   mask, it will (correctly) alarm** — silence it or get off the plug.

## Gotchas at a new location
- **Plug won't join `cpap`:** ESP8266 Tasmota needs the Pi's AP on **WPA2-AES with PMF
  disabled** (already configured on `cpap-ap`; see AGENTS.md if it ever regresses).
  Power-cycle the plug once; it should auto-rejoin.
- **Configuring a *fresh* plug abroad:** phones refuse to hold a no-internet setup AP —
  configure from a computer, or point it at `cpap` via
  `curl "http://192.168.4.1/cm?cmnd=Backlog%20SSID1%20cpap%3BPassword1%20<pw>%3BRestart%201"`.
- **No `wlan0` after boot:** the Pi's Wi-Fi firmware can corrupt (no AP, plug
  unreachable). Fix: `sudo apt reinstall firmware-brcm80211` + reboot (see AGENTS.md).
- **Different outlet/adapter:** a travel *adapter* is fine (it doesn't change voltage);
  just make sure the Tasmota itself is rated for 220–240 V.
