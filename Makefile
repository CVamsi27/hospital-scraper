# Docita SMS outreach — single-command runner.
# All targets wrap scripts/sms_send.py. See docs/SMS_OUTREACH_PLAN.md for the full plan.
#
# Required env (.env): SMS_GATEWAY_URL, SMS_GATEWAY_USER, SMS_GATEWAY_PASS, OWNER_PHONE_E164
# Run `make help` for the live list of targets.

SHELL := /bin/bash
PY    ?= python3

# Per-run knobs (override on the CLI, e.g. `make sms-clinics LIMIT=50`)
LIMIT          ?= 500
SMS_DELAY      ?= 4
TEMPLATE_CLINIC       ?= docita_hms_sms_v1
TEMPLATE_PHARMACY     ?= docita_pharmacy_sms_v1
TEMPLATE_DIAGNOSTIC   ?= docita_diagnostics_sms_v1
TEMPLATE_PARTNER      ?= docita_partner_sms_v1
TEMPLATE_MARKETING    ?= docita_marketing_sms_v1
ALLOW_OFFHOURS ?= 0

CSV_CLINIC     ?= leads_hospitals_clinics.csv
CSV_PHARMACY   ?= leads_pharmacies.csv
CSV_DIAGNOSTIC ?= leads_diagnostics.csv
CSV_PARTNER    ?= partners.csv
CSV_MARKETING  ?= marketing_leads.csv

# Wrapper that auto-loads .env, then forwards everything to the runner.
# Each target appends its own CLI args after `$(PY) scripts/sms_send.py`.
# (Using a here-doc keeps make from eating the args.)
RUNNER = if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
         export ALLOW_OFFHOURS=$(ALLOW_OFFHOURS); \
         $(PY) scripts/sms_send.py

.DEFAULT_GOAL := help

.PHONY: help install env doctor test-doctor
help:           ## Show this help.
	@awk 'BEGIN {FS = ":.*##"; printf "Docita SMS outreach — single-command runner.\n\nUsage:\n  make <target> [LIMIT=50] [SMS_DELAY=2] [TEMPLATE=...] [ALLOW_OFFHOURS=1]\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:        ## pip install -r requirements.txt
	$(PY) -m pip install -r requirements.txt

env:            ## Copy .env.example to .env if missing.
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env from .env.example — fill in SMS_GATEWAY_URL/USER/PASS + OWNER_PHONE_E164."; else echo ".env already exists."; fi

doctor:         ## Ping the phone's local SMS gateway (no SMS sent).
	if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	$(PY) scripts/sms_doctor.py

doctor-help:    ## Print a checklist for fixing common local-gateway issues.
	@echo "Phone-side checklist (open the SMS Gateway app on the phone):"
	@echo "  1. Local Server toggle is ON"
	@echo "  2. Bottom button shows 'Online' (not 'Offline')"
	@echo "  3. Settings → Local Server has a username (≥3 chars) and password (≥8 chars)"
	@echo "  4. The phone's local IP (e.g. 192.168.1.50) is what you put in SMS_GATEWAY_URL"
	@echo ""
	@echo "Mac-side checklist:"
	@echo "  1. Mac is on the same WiFi as the phone"
	@echo "  2. Quick test:  curl -u USER:PASS http://PHONE_IP:8080/message"
	@echo "     Expected: 200 (or 401/403 if creds are off) — anything else means unreachable"

test-doctor:    ## Send 1 test SMS to OWNER_PHONE_E164 — proves the pipe.
		if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
		$(PY) scripts/sms_test.py

# ---- Dry-runs (no SMS) --------------------------------------------------------
.PHONY: sms-preview-clinics sms-preview-pharmacies sms-preview-diagnostics sms-preview-partners sms-preview-marketing sms-preview-all
sms-preview-clinics:        ## Preview first 10 clinic messages (no send).
	$(RUNNER) --csv $(CSV_CLINIC) --campaign clinic --template $(TEMPLATE_CLINIC) --limit 10

sms-preview-pharmacies:     ## Preview first 10 pharmacy messages (no send).
	$(RUNNER) --csv $(CSV_PHARMACY) --campaign pharmacy --template $(TEMPLATE_PHARMACY) --limit 10

sms-preview-diagnostics:    ## Preview first 10 diagnostic messages (no send).
		$(RUNNER) --csv $(CSV_DIAGNOSTIC) --campaign diagnostic --template $(TEMPLATE_DIAGNOSTIC) --limit 10

sms-preview-partners:       ## Preview first 10 partner messages (no send).
		$(RUNNER) --csv $(CSV_PARTNER) --campaign partner --template $(TEMPLATE_PARTNER) --all-leads --limit 10

sms-preview-marketing:      ## Preview first 10 marketing messages (no send).
		$(RUNNER) --csv $(CSV_MARKETING) --campaign marketing --template $(TEMPLATE_MARKETING) --all-leads --limit 10

sms-preview-all: sms-preview-clinics sms-preview-pharmacies sms-preview-diagnostics ## Preview all three audiences.

# ---- Live sends (with confirmation prompt) -----------------------------------
.PHONY: sms-clinics sms-pharmacies sms-diagnostics sms-partners sms-marketing sms-all
sms-clinics:       ## Send to T1 clinics (default 500 cap, asks y/N before sending).
	$(RUNNER) --csv $(CSV_CLINIC) --campaign clinic --template $(TEMPLATE_CLINIC) \
	          --limit $(LIMIT) --delay $(SMS_DELAY) --confirm

sms-pharmacies:    ## Send to T1 pharmacies.
	$(RUNNER) --csv $(CSV_PHARMACY) --campaign pharmacy --template $(TEMPLATE_PHARMACY) \
	          --limit $(LIMIT) --delay $(SMS_DELAY) --confirm

sms-diagnostics:   ## Send to T1 diagnostics.
		$(RUNNER) --csv $(CSV_DIAGNOSTIC) --campaign diagnostic --template $(TEMPLATE_DIAGNOSTIC) \
		          --limit $(LIMIT) --delay $(SMS_DELAY) --confirm

sms-partners:      ## Send approved partner invitations (all rows; configure CSV_PARTNER).
		$(RUNNER) --csv $(CSV_PARTNER) --campaign partner --template $(TEMPLATE_PARTNER) --all-leads \
		          --limit $(LIMIT) --delay $(SMS_DELAY) --confirm

sms-marketing:     ## Send approved marketing-partner invitations (all rows; configure CSV_MARKETING).
		$(RUNNER) --csv $(CSV_MARKETING) --campaign marketing --template $(TEMPLATE_MARKETING) --all-leads \
		          --limit $(LIMIT) --delay $(SMS_DELAY) --confirm

sms-all: sms-clinics sms-pharmacies sms-diagnostics ## Send to all three audiences, in order.

# ---- Force sends (no prompt) -------------------------------------------------
.PHONY: sms-clinics-force sms-pharmacies-force sms-diagnostics-force sms-partners-force sms-marketing-force
sms-clinics-force:        ## Send to T1 clinics, no prompt, re-send already-sent rows.
	$(RUNNER) --csv $(CSV_CLINIC) --campaign clinic --template $(TEMPLATE_CLINIC) \
	          --limit $(LIMIT) --delay $(SMS_DELAY) --confirm --force

sms-pharmacies-force:     ## Send to T1 pharmacies, no prompt.
	$(RUNNER) --csv $(CSV_PHARMACY) --campaign pharmacy --template $(TEMPLATE_PHARMACY) \
	          --limit $(LIMIT) --delay $(SMS_DELAY) --confirm --force

sms-diagnostics-force:    ## Send to T1 diagnostics, no prompt.
		$(RUNNER) --csv $(CSV_DIAGNOSTIC) --campaign diagnostic --template $(TEMPLATE_DIAGNOSTIC) \
		          --limit $(LIMIT) --delay $(SMS_DELAY) --confirm --force

sms-partners-force:       ## Send partner invitations, no prompt, re-send accepted rows.
		$(RUNNER) --csv $(CSV_PARTNER) --campaign partner --template $(TEMPLATE_PARTNER) --all-leads \
		          --limit $(LIMIT) --delay $(SMS_DELAY) --confirm --force

sms-marketing-force:      ## Send marketing-partner invitations, no prompt, re-send accepted rows.
		$(RUNNER) --csv $(CSV_MARKETING) --campaign marketing --template $(TEMPLATE_MARKETING) --all-leads \
		          --limit $(LIMIT) --delay $(SMS_DELAY) --confirm --force

# ---- Operational --------------------------------------------------------------
.PHONY: sms-status sms-delivery-status enrich-contacts referral-codes sms-suppress-list sms-inbox-once sms-inbox-poll partner-signups notify-test
sms-status:        ## Print sent/failed counts per lead CSV.
		@for f in $(CSV_CLINIC) $(CSV_PHARMACY) $(CSV_DIAGNOSTIC) $(CSV_PARTNER) $(CSV_MARKETING); do \
		  if [ ! -f "$$f" ]; then continue; fi; \
		  echo "== $$f =="; \
		  $(PY) -c "import csv; rows=list(csv.DictReader(open('$$f'))); from collections import Counter; c=Counter((r.get('sms_status') or 'never').strip() for r in rows); [print(f'  {k:10s} {v}') for k,v in sorted(c.items())]"; \
		done

sms-delivery-status: ## Summarize gateway-accepted and failed delivery attempts.
		@if [ -f sms_delivery_log.csv ]; then \
		  $(PY) -c "import csv; from collections import Counter; rows=list(csv.DictReader(open('sms_delivery_log.csv'))); c=Counter((r.get('delivery_status') or 'unknown').strip() for r in rows); [print(f'{k:20s} {v}') for k,v in sorted(c.items())]"; \
		else echo "No sms_delivery_log.csv yet. No live sends have been recorded."; fi

enrich-contacts: ## Add public phone_numbers and phone_sources from official websites.
		$(PY) scripts/enrich_contacts.py --csv $(CSV_CLINIC) --csv $(CSV_PHARMACY) --csv $(CSV_DIAGNOSTIC) \
			--workers 4 --delay-ms 100

referral-codes:    ## Rebuild referral_codes.csv from lead CSVs (one per row).
	$(PY) scripts/build_referral_codes.py \
		--clinic-csv $(CSV_CLINIC) \
		--pharmacy-csv $(CSV_PHARMACY) \
		--diagnostic-csv $(CSV_DIAGNOSTIC) \
		--out referral_codes.csv

sms-suppress-list: ## (Re)build empty do_not_sms.csv template. Manual fill for now.
	@if [ ! -f do_not_sms.csv ]; then \
	  echo "phone,reason,added_at" > do_not_sms.csv && echo "Created empty do_not_sms.csv — fill STOP replies here."; \
	else \
	  echo "do_not_sms.csv already exists. Edit it manually for now."; \
	fi

sms-inbox-once:     ## Single pass over the phone's inbox (process STOP / JOIN / replies, then exit).
	if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	$(PY) scripts/sms_inbox.py --once

sms-inbox-poll:     ## Poll the phone's inbox every 60s. Run in its own terminal.
	if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	$(PY) scripts/sms_inbox.py --interval 60

partner-signups:    ## Show everyone who replied JOIN/REFER (partner signup intents).
	@if [ -f partner_signups.csv ]; then \
	  echo "== partner_signups.csv =="; \
	  column -t -s, partner_signups.csv 2>/dev/null || cat partner_signups.csv; \
	else \
	  echo "(empty — no partner signup replies yet)"; \
	fi

notify-test:        ## Fire a test notification through all enabled channels.
	if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	$(PY) scripts/sms_notify.py --action reply --phone "+919876543210" \
		--name "Test Hospital" --text "This is a test notification." --campaign clinic
