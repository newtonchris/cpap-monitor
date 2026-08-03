# network/ — the Pi-hosted plug Wi-Fi network (AP + DHCP)

The smart plugs do **not** live on the house Wi-Fi. The Pi hosts its own
access point (SSID `cpap` by convention) and is the DHCP server for the
plugs. This directory makes that whole network reproducible on a fresh
Pi with one command — the Python app alone is not a working monitor if
the plugs have nothing to join.

## Topology

```
house router ──ethernet── eth0 ┐
                               │  Raspberry Pi
   smart plugs ══Wi-Fi══ wlan0 ┘  (AP `cpap`, 192.168.77.1/24,
                                   DHCP + NAT via NM "shared" mode)
```

* **Wired uplink (`eth0`)** carries internet/SSH from the house LAN.
* **`wlan0` hosts the plug net** — an isolated 192.168.77.0/24 island.
  The alarm loop (Pi ↔ plug) survives the house router dying, and the
  whole rig is portable: hotel room, Iceland, anywhere with a wall socket.

This is a **NetworkManager AP-mode connection named `cpap-ap`**, not
standalone hostapd/dnsmasq. `ipv4.method shared` makes NM run its own
dnsmasq (DHCP + DNS + NAT); extra dnsmasq options — our static plug
reservations — go in `/etc/NetworkManager/dnsmasq-shared.d/`. Leases:
`/var/lib/NetworkManager/dnsmasq-wlan0.leases`.

## Usage

```bash
cp network/network.env.example network/network.env
$EDITOR network/network.env        # SSID, PSK, plug MAC → IP pins
sudo network/setup-network.sh      # or: make network
```

`network.env` is **gitignored** — it holds the Wi-Fi passphrase and this
repo is public. Re-running the script is safe and is also how you apply
changes (edit `network.env`, re-run; it converges the connection and
regenerates the drop-in).

## The settings that must not change

* **WPA2-AES with PMF disabled** (`key-mgmt=wpa-psk, proto=rsn,
  pairwise=ccmp, group=ccmp, pmf=1(disable)`). ESP8266 Tasmota plugs
  will not associate with anything fancier. Hard-won; the script sets it
  explicitly so an NM upgrade's new defaults can't quietly break it.
* **Band bg, 2.4 GHz** — the plugs have no 5 GHz radio.
* **Static DHCP reservations per plug MAC** (the generated
  `dnsmasq-shared.d/cpap-plugs.conf`). An unpinned plug that drifts to a
  new lease breaks the monitor's configured `host` — IP drift is how the
  predecessor alarm was silently dead for 78 days. Convention: `.11`
  Tasmota; keep `[tasmota] host` in
  `/etc/cpap-monitor/config.toml` matching.

## Gotchas

* **No `wlan0` at all?** Corrupted Broadcom firmware (seen in the field):
  `sudo apt install --reinstall firmware-brcm80211 && sudo reboot`. The
  script detects the missing interface and stops; it deliberately does
  not auto-reinstall firmware or reboot your Pi.
* Reservations only apply on (re)activation — the script bounces
  `cpap-ap` for you; plugs rejoin within ~30 s.
* Don't configure plugs from a phone — phones bounce off a no-internet
  setup hotspot. Use a laptop or the Pi.

## This whole AP is OPTIONAL

For a stationary home box you can skip this directory entirely: put the
plug on the house Wi-Fi, give it a reservation on your router (pin it!
see above), and point `/etc/cpap-monitor/config.toml` at that IP. The
Pi-hosted AP buys isolation from the house router and travel portability
— see the hardware note in the top-level [README](../README.md).
