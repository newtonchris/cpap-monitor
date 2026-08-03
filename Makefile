# cpap-monitor — top-level Makefile
#
# Self-documenting: `make` or `make help` lists every target. Targets are
# thin wrappers over scripts/ and the `python3 -m cpapmon` CLI — the logic
# lives there, not here. Works run as root OR as a normal user (sudo is
# prepended automatically where needed), so it serves both a human at the
# keyboard and an AI agent driving the Pi over SSH (see AGENTS.md).
#
# Fresh-Pi order (details in docs/DEPLOY.md):
#   make deps network install config   → edit /etc/cpap-monitor/config.toml
#   make validate check siren test-alert arm status

.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

# Prepend sudo only when not already root, so every target works both ways.
SUDO := $(shell [ "$$(id -u)" = 0 ] || echo sudo)

CONFIG      := /etc/cpap-monitor/config.toml
DEPLOY_USER := cnewton
HEARTBEAT   := logs/status.json

.PHONY: help deps network install config siren validate check simulate \
        test-alert arm restart status test teardown-access

help:  ## List all targets (default)
	@echo "cpap-monitor — targets:"
	@grep -hE '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
	    | awk 'BEGIN {FS = ":.*##"} {printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Fresh Pi: deps → network → install → config → (edit config) →"
	@echo "          validate → check → siren → test-alert → arm → status"

# ---------------------------------------------------------------- setup ----

deps:  ## Install OS + Python dependencies (apt: sox etc.; pip: requirements)
	$(SUDO) apt-get update
	$(SUDO) apt-get install -y git python3-pip sox libsox-fmt-mp3 libsox-fmt-all
	pip install -r requirements.txt --break-system-packages

network:  ## Bring up the Pi-hosted plug Wi-Fi AP + DHCP (needs network/network.env)
	$(SUDO) network/setup-network.sh

install:  ## Install/refresh systemd unit + /etc/cpap-monitor skeleton (idempotent)
	$(SUDO) scripts/install.sh

config:  ## Seed /etc/cpap-monitor/config.toml from the example if absent (never overwrites)
	$(SUDO) install -d -m 0750 -o root -g $(DEPLOY_USER) $(dir $(CONFIG))
	@if $(SUDO) test -f $(CONFIG); then \
	    echo "$(CONFIG) exists — leaving it alone."; \
	else \
	    $(SUDO) install -m 0640 -o root -g $(DEPLOY_USER) \
	        config/config.example.toml $(CONFIG); \
	    echo ">>> Seeded $(CONFIG) — EDIT IT and replace every REPLACE_ME. <<<"; \
	fi

siren:  ## Generate the default escalating alarm siren (sounds/alarm.wav)
	scripts/make-siren.sh

# ------------------------------------------------------------- checking ----

validate:  ## Sanity-check the config (systemd gates startup on this)
	python3 -m cpapmon validate

check:  ## Read the plug once and print watts/band
	python3 -m cpapmon check

simulate:  ## Dry-run the alarm state machine on a canned die-and-recover scenario
	python3 -m cpapmon simulate

test-alert:  ## Fire the REAL siren + push for a few seconds (run monthly)
	python3 -m cpapmon test-alert

test:  ## Run the pure-logic pytest suite (no hardware needed)
	python3 -m pytest tests/

# ------------------------------------------------------------ operating ----

arm:  ## (Re)start the monitor service and show the ARMED evidence
	$(SUDO) systemctl restart cpap-monitor
	@sleep 3
	$(SUDO) systemctl --no-pager --lines=8 status cpap-monitor
	@echo "Look for the 'ARMED' line; follow with: journalctl -u cpap-monitor -f"

restart: arm  ## Alias for `arm`

status:  ## Service state + heartbeat freshness (anti-silent-failure check)
	@$(SUDO) systemctl --no-pager --lines=3 status cpap-monitor || true
	@echo "--- heartbeat ($(HEARTBEAT)) ---"
	@if [ -f $(HEARTBEAT) ]; then \
	    cat $(HEARTBEAT); echo; \
	    echo "age: $$(( $$(date +%s) - $$(stat -c %Y $(HEARTBEAT)) ))s" \
	         "(stale if >60s — heartbeat refreshes every 30s)"; \
	else \
	    echo "NO HEARTBEAT FILE — the monitor is NOT confirming life."; \
	    exit 1; \
	fi

# -------------------------------------------------------------- cleanup ----

teardown-access:  ## Remove temporary agent SSH keys + NOPASSWD sudo (MARKER=<str> CONFIRM=yes)
	@# Per AGENTS.md: setup is driven from a bigger machine over SSH via a
	@# temporary key; tear that access down when finished. Dry-runs by
	@# default. Example:  make teardown-access MARKER=cpap1_pi_temp CONFIRM=yes
	@if [ -z "$(MARKER)" ]; then \
	    echo "Usage: make teardown-access MARKER=<substring-of-temp-key-comment> [CONFIRM=yes]"; \
	    echo; \
	    echo "Review current access grants:"; \
	    for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do \
	        $(SUDO) test -f $$f && { echo "--- $$f:"; $(SUDO) cut -c1-60 $$f | sed 's/$$/.../'; }; \
	    done; \
	    echo "--- /etc/sudoers.d/:"; $(SUDO) ls -l /etc/sudoers.d/; \
	    exit 1; \
	fi
	@for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do \
	    $(SUDO) test -f $$f || continue; \
	    if $(SUDO) grep -n -- "$(MARKER)" $$f; then \
	        if [ "$(CONFIRM)" = "yes" ]; then \
	            $(SUDO) sed -i "/$(MARKER)/d" $$f && echo "REMOVED matching key line(s) from $$f"; \
	        else \
	            echo "would remove the above from $$f (re-run with CONFIRM=yes)"; \
	        fi; \
	    fi; \
	done
	@for f in /etc/sudoers.d/*; do \
	    $(SUDO) test -f $$f || continue; \
	    case "$$f" in */README) continue;; esac; \
	    if echo "$$f" | grep -q -- "$(MARKER)" || $(SUDO) grep -l NOPASSWD $$f | grep -q .; then \
	        echo "sudoers drop-in: $$f"; $(SUDO) cat $$f; \
	        if [ "$(CONFIRM)" = "yes" ] && echo "$$f" | grep -q -- "$(MARKER)"; then \
	            $(SUDO) rm -v $$f; \
	        else \
	            echo "  (only removed automatically if filename matches MARKER + CONFIRM=yes)"; \
	        fi; \
	    fi; \
	done
	@echo "Finally, re-tighten sshd yourself (key-only auth, no root password login)."
