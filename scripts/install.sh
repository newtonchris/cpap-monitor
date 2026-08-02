#!/usr/bin/env bash
# cpap-monitor installer — idempotent; safe to re-run after every git pull.
#
# What it does:
#   1. installs OS dependencies (sox for the siren)
#   2. creates /etc/cpap-monitor and seeds config.toml from the example
#      (never overwrites an existing config), perms 0640 root:cnewton
#   3. installs + enables the systemd unit
#
# What it does NOT do:
#   - fill in secrets (you edit /etc/cpap-monitor/config.toml yourself)
#   - copy an alarm mp3 (gitignored; bring your own — see docs/DEPLOY.md)
#   - start the service if the config is still invalid (validate gates it)
#
# Run from the repo checkout:  sudo scripts/install.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_USER="cnewton"
CONFIG_DIR="/etc/cpap-monitor"
CONFIG_FILE="${CONFIG_DIR}/config.toml"
UNIT_SRC="${REPO_DIR}/systemd/cpap-monitor.service"
UNIT_DST="/etc/systemd/system/cpap-monitor.service"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root (sudo scripts/install.sh)" >&2
    exit 1
fi

echo "==> cpap-monitor install from ${REPO_DIR}"

# 1. OS dependencies -----------------------------------------------------
echo "==> Installing OS packages (sox + mp3 support)..."
apt-get install -y --no-upgrade sox libsox-fmt-mp3 >/dev/null

# 2. Config directory + seed config --------------------------------------
echo "==> Ensuring ${CONFIG_DIR} ..."
install -d -m 0750 -o root -g "${DEPLOY_USER}" "${CONFIG_DIR}"

if [[ -f "${CONFIG_FILE}" ]]; then
    echo "    ${CONFIG_FILE} exists — leaving it alone."
else
    install -m 0640 -o root -g "${DEPLOY_USER}" \
        "${REPO_DIR}/config/config.example.toml" "${CONFIG_FILE}"
    echo "    Seeded ${CONFIG_FILE} from the example."
    echo "    >>> EDIT IT NOW and replace every REPLACE_ME. <<<"
fi
# Enforce perms even on pre-existing configs (idempotent hardening).
chown root:"${DEPLOY_USER}" "${CONFIG_FILE}"
chmod 0640 "${CONFIG_FILE}"

# 3. systemd unit ---------------------------------------------------------
echo "==> Installing systemd unit..."
install -m 0644 "${UNIT_SRC}" "${UNIT_DST}"
systemctl daemon-reload
systemctl enable cpap-monitor >/dev/null 2>&1 || systemctl enable cpap-monitor

# 4. Gate the (re)start on a valid config ---------------------------------
echo "==> Validating config..."
if (cd "${REPO_DIR}" && sudo -u "${DEPLOY_USER}" python3 -m cpapmon validate \
        --config "${CONFIG_FILE}") 2>&1 | sed 's/^/    /'; then
    echo "==> Config valid — (re)starting service..."
    systemctl restart cpap-monitor
    systemctl --no-pager --lines=5 status cpap-monitor || true
else
    echo "==> Config NOT valid yet. Fix ${CONFIG_FILE}, then:"
    echo "        python3 -m cpapmon validate"
    echo "        sudo systemctl restart cpap-monitor"
fi

echo "==> Done. Verify the plug with:  python3 -m cpapmon check"
