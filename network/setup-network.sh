#!/usr/bin/env bash
# setup-network.sh — reproduce the Pi-hosted plug Wi-Fi network (AP + DHCP).
#
# Idempotent; safe to re-run. Creates (or converges) the NetworkManager
# connection `cpap-ap` with the field-proven settings, and installs a
# static-lease drop-in so the plugs always get the same IPs.
#
# This is NetworkManager AP mode, NOT standalone hostapd/dnsmasq:
#   * `ipv4.method shared` makes NM run its own dnsmasq for the subnet
#     (DHCP + DNS + NAT out the wired uplink). Leases land in
#     /var/lib/NetworkManager/dnsmasq-wlan0.leases.
#   * Extra dnsmasq options for shared mode — our static reservations —
#     go in /etc/NetworkManager/dnsmasq-shared.d/*.conf.
#
# HARD-WON SECURITY SETTINGS (do not "modernize" these):
#   ESP8266 Tasmota plugs associate ONLY with WPA2-AES and PMF (802.11w)
#   DISABLED. That is exactly: key-mgmt=wpa-psk, proto=rsn,
#   pairwise=ccmp, group=ccmp, pmf=1 (1 means DISABLE in nmcli).
#   WPA3/SAE-mixed or PMF-optional and the plug joins-then-drops forever.
#
# PREREQUISITE (checked, not auto-fixed): the Wi-Fi radio must exist and
# be up. If there is NO wlan0 at all, the Broadcom firmware has likely
# corrupted — `sudo apt install --reinstall firmware-brcm80211 && sudo
# reboot` (seen in the field; see docs/DEPLOY.md).
#
# Site values (SSID/PSK/subnet/reservations) come from network/network.env
# (gitignored — copy network.env.example and fill it in). The PSK never
# lives in this repo.
#
# Usage:  sudo network/setup-network.sh      (or: make network)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/network.env"
CON_NAME="cpap-ap"
DROPIN_DIR="/etc/NetworkManager/dnsmasq-shared.d"
DROPIN_FILE="${DROPIN_DIR}/cpap-plugs.conf"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root (sudo network/setup-network.sh)" >&2
    exit 1
fi

# 0. Site config ----------------------------------------------------------
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "ERROR: ${ENV_FILE} not found." >&2
    echo "       cp ${SCRIPT_DIR}/network.env.example ${ENV_FILE}" >&2
    echo "       then edit it (SSID, PSK, plug MACs) and re-run." >&2
    exit 1
fi
# shellcheck source=network.env.example
source "${ENV_FILE}"

: "${CPAP_AP_SSID:?network.env must set CPAP_AP_SSID}"
: "${CPAP_AP_PSK:?network.env must set CPAP_AP_PSK}"
: "${CPAP_AP_IFACE:=wlan0}"
: "${CPAP_AP_CHANNEL:=6}"
: "${CPAP_AP_ADDR:=192.168.77.1/24}"
: "${CPAP_PLUG_RESERVATIONS:=}"

if [[ "${CPAP_AP_PSK}" == "REPLACE_ME" ]]; then
    echo "ERROR: CPAP_AP_PSK is still REPLACE_ME — edit ${ENV_FILE}." >&2
    exit 1
fi
if (( ${#CPAP_AP_PSK} < 8 || ${#CPAP_AP_PSK} > 63 )); then
    echo "ERROR: WPA2 passphrase must be 8-63 characters." >&2
    exit 1
fi

echo "==> Plug network setup: SSID '${CPAP_AP_SSID}' on ${CPAP_AP_IFACE}," \
     "${CPAP_AP_ADDR} shared (NM dnsmasq DHCP)"

# 1. Prerequisites ---------------------------------------------------------
command -v nmcli >/dev/null || {
    echo "ERROR: nmcli not found — this setup requires NetworkManager" \
         "(Raspberry Pi OS Bookworm default)." >&2
    exit 1
}
if ! nmcli device status | awk '{print $1}' | grep -qx "${CPAP_AP_IFACE}"; then
    echo "ERROR: interface ${CPAP_AP_IFACE} does not exist." >&2
    echo "       On a Pi, a missing wlan0 usually means corrupted Broadcom" >&2
    echo "       Wi-Fi firmware. Fix manually (NOT auto-fixed here):" >&2
    echo "           sudo apt install --reinstall firmware-brcm80211" >&2
    echo "           sudo reboot" >&2
    exit 1
fi
nmcli radio wifi on   # idempotent; un-soft-blocks the radio

# 2. The cpap-ap connection (create or converge) ----------------------------
# All properties are applied on every run, so a drifted connection is pulled
# back to the proven state. `autoconnect-priority 100` beats any stale house
# Wi-Fi profile that might otherwise grab wlan0 after a reboot.
if ! nmcli -g NAME con show | grep -qx "${CON_NAME}"; then
    echo "==> Creating connection ${CON_NAME}..."
    nmcli con add type wifi ifname "${CPAP_AP_IFACE}" con-name "${CON_NAME}" \
        autoconnect yes ssid "${CPAP_AP_SSID}" >/dev/null
else
    echo "==> Connection ${CON_NAME} exists — converging its settings..."
fi

# NOTE: the PSK passes through this process's argv (briefly visible in
# `ps`). Accepted on a single-user bedroom Pi; nmcli offers no stdin path.
nmcli con modify "${CON_NAME}" \
    connection.interface-name "${CPAP_AP_IFACE}" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
    802-11-wireless.ssid "${CPAP_AP_SSID}" \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    802-11-wireless.channel "${CPAP_AP_CHANNEL}" \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.proto rsn \
    802-11-wireless-security.pairwise ccmp \
    802-11-wireless-security.group ccmp \
    802-11-wireless-security.pmf 1 \
    802-11-wireless-security.psk "${CPAP_AP_PSK}" \
    ipv4.method shared \
    ipv4.addresses "${CPAP_AP_ADDR}" \
    ipv6.method disabled
echo "    settings applied (WPA2-AES/RSN-CCMP, PMF disabled, ipv4 shared)."

# 3. Static DHCP reservations (plug pinning) --------------------------------
echo "==> Writing static-lease drop-in ${DROPIN_FILE}..."
install -d -m 0755 "${DROPIN_DIR}"
TMP="$(mktemp)"
{
    echo "# cpap-monitor plug reservations — GENERATED by network/setup-network.sh"
    echo "# from network/network.env. Edit THAT file and re-run; do not hand-edit."
    while IFS= read -r line; do
        line="${line%%#*}"                       # strip comments
        line="$(echo "${line}" | tr -d '[:space:]')"
        [[ -z "${line}" ]] && continue
        mac="${line%%=*}"
        rest="${line#*=}"
        ip="${rest%%,*}"
        name="${rest#*,}"
        if [[ ! "${mac}" =~ ^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$ ]]; then
            echo "ERROR: bad MAC in CPAP_PLUG_RESERVATIONS: '${mac}'" >&2
            exit 1
        fi
        echo "dhcp-host=${mac},${ip},${name},infinite"
    done <<< "${CPAP_PLUG_RESERVATIONS}"
} > "${TMP}"
install -m 0644 "${TMP}" "${DROPIN_FILE}"
rm -f "${TMP}"
sed 's/^/    /' "${DROPIN_FILE}"

# Warn about OTHER drop-ins that might carry conflicting/duplicate pins
# (e.g. a hand-made drop-in left over from an earlier build).
for f in "${DROPIN_DIR}"/*.conf; do
    [[ "${f}" == "${DROPIN_FILE}" ]] && continue
    [[ -e "${f}" ]] || continue
    if grep -q '^dhcp-host=' "${f}"; then
        echo "    WARNING: ${f} also contains dhcp-host lines — review for"
        echo "             duplicates/conflicts with ${DROPIN_FILE}."
    fi
done

# 4. (Re)activate ------------------------------------------------------------
# NM spawns the shared-mode dnsmasq at activation, so a down/up cycle is
# required for new reservations to take effect. Plugs re-associate within
# ~10-30 s on their own.
echo "==> (Re)activating ${CON_NAME} (plugs will briefly drop and rejoin)..."
nmcli con down "${CON_NAME}" >/dev/null 2>&1 || true
nmcli con up "${CON_NAME}" >/dev/null
sleep 2

# 5. Verify -------------------------------------------------------------------
echo "==> Verifying..."
state="$(nmcli -g GENERAL.STATE con show "${CON_NAME}" 2>/dev/null || true)"
addr="$(ip -4 -o addr show "${CPAP_AP_IFACE}" | awk '{print $4}')"
echo "    connection state: ${state:-unknown}"
echo "    ${CPAP_AP_IFACE} address: ${addr:-NONE}"
if [[ "${addr}" != "${CPAP_AP_ADDR}" ]]; then
    echo "ERROR: ${CPAP_AP_IFACE} is not holding ${CPAP_AP_ADDR}." >&2
    echo "       nmcli con show ${CON_NAME}   and   journalctl -u NetworkManager" >&2
    exit 1
fi

echo "==> Done. AP '${CPAP_AP_SSID}' is up on ${CPAP_AP_IFACE} (${CPAP_AP_ADDR})."
echo "    Watch plugs join:  watch cat /var/lib/NetworkManager/dnsmasq-${CPAP_AP_IFACE}.leases"
echo "    Then verify end-to-end:  python3 -m cpapmon check"
