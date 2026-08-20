# SMS Outreach Plan — Docita (Hyderabad Lead Lists) — **local-only**

**Owner:** Vamsi · **Repo:** `hospital-scraper/`
**Architecture:** Mac (this script) + Android phone (SMS Gateway app in local mode) on the same WiFi. **No cloud, no Firebase, no server, no tunnel.** Phone and Mac only.
**Single command:** `make sms-clinics` (or any of the variants below).
**Last updated:** 2026-08-03

---

## 1. Goal

Drive clinic / hospital demos and qualified partner signups using the lead CSVs
in this repo, using
one Android phone with an unlimited-SIM plan as the gateway (no per-message
cost, no third-party service).

---

## 2. Gateway — what we picked and why

**Picked: [capcom6/android-sms-gateway](https://github.com/capcom6/android-sms-gateway)** in **Local Server** mode.

| Gateway | Cloud? | Firebase? | Local-only? | API | Notes |
|---|---|---|---|---|---|
| **[capcom6 SMS Gateway (local mode)](https://github.com/capcom6/android-sms-gateway)** ✅ | no | no | **yes** | `POST /message` + basic auth | The only "phone + Mac, no cloud" option that just works. |
| vernu/textbee | yes (free beta) | yes | no | REST + API key | Was our first pick; cloud-based, so dropped. |
| NdoleStudio/httpsms | yes | yes | no | REST + API key | Cloud-based, dropped. |
| Twilio / MSG91 / Razorpay SMS | yes | n/a | no | REST | Per-message billing — defeats the point. |

### Why capcom6 local mode is the right answer for "phone + Mac only"

- The app runs a tiny HTTP server **on the phone itself** (default port 8080).
- Mac → HTTP over your local WiFi → phone → carrier SMS.
- No account, no signup, no API key from a third party.
- No internet needed for the SMS path. (WiFi is still needed for Mac↔phone.)
- Carrier-side limits (100–200 SMS/hour/SIM) still apply, and we throttle to
  4s/msg = 15/min to stay well under.

### Setup (one-time, ~5 min)

1. On the Android phone, install **SMS Gateway for Android** from the Play
   Store (search for "SMS Gateway for Android" by capcom6, or grab the APK
   from [github.com/capcom6/android-sms-gateway/releases](https://github.com/capcom6/android-sms-gateway/releases)).
2. Open the app. Go to **Settings → Local Server**:
   - Port: `8080` (default)
   - Username: anything ≥ 3 chars
   - Password: anything ≥ 8 chars
3. Back on the **Home** tab, flip the **Local Server** toggle **ON**, then tap
   the **Offline** button at the bottom so it becomes **Online**. The phone
   will show its local IP (e.g. `192.168.1.50`).
4. Plug those three values into `hospital-scraper/.env`:

   ```
   SMS_GATEWAY_URL=http://192.168.1.50:8080
   SMS_GATEWAY_USER=<whatever you set in step 2>
   SMS_GATEWAY_PASS=<whatever you set in step 2>
   ```

5. Make sure the phone and Mac are on the same WiFi, then:

   ```bash
   make doctor           # pings the phone — should print OK
   make test-doctor      # sends 1 SMS to your own number
   ```

### When this breaks (and how to fix it fast)

| Symptom | Likely cause | Fix |
|---|---|---|
| `make doctor` → `Connection refused` | phone app not Online, or different WiFi | toggle ON, tap Online; check WiFi |
| `make doctor` → 401 / 403 | wrong username/password in `.env` | re-enter from app Settings → Local Server |
| `make doctor` → timeout | phone's firewall, or wrong IP | curl the URL from Mac Terminal; if that fails, find the phone's IP again (it can change after reboot) |
| `make doctor` → OK but `make sms-clinics` fails mid-way | phone went to sleep / app backgrounded | open the app, keep it foregrounded during sends; Android may kill background services on some OEMs (Xiaomi, Vivo) — disable battery optimization for the app |

> **OEM battery optimization is the #1 reason this kind of setup fails.**
> On the phone, go to Settings → Apps → SMS Gateway → Battery → **Unrestricted**.

---

## 3. Audiences & lead files

| Audience | Lead CSV | Rows | Tier filter | Why they matter |
|---|---|---|---|---|
| **Clinics & hospitals** | `leads_hospitals_clinics.csv` | ~14,292 | T1 | Primary buyers. Demo + 15-min walkthrough. |
| **Pharmacies** | `leads_pharmacies.csv` | ~4,913 | T1 | Referral partners — proposed 10% of net collected revenue for the first 12 billed months. |
| **Diagnostics** | `leads_diagnostics.csv` | ~1,837 | T1 | Referral partners — proposed 10% of net collected revenue for the first 12 billed months. |
| **Partners** | `partners.csv` | configured | all | Approved partner invitations; pass `CSV_PARTNER=...`. |
| **Marketing partners** | `marketing_leads.csv` | configured | all | Approved field/marketing invitations; pass `CSV_MARKETING=...`. |
| (Future) Doctor networks | `leads_nearby.csv` | ~15,653 | T1 | Add a 4th audience when needed. |

T1 is the only safe starting filter — your scraper already tags
`demo_tier = T1` for the leads with the highest signal.

---

## 4. Referral mechanics and link safety

Pulled from `docita/apps/api/src/domains/subscriptions/core/services/referral-reward.rules.ts`:

- **Clinic → Clinic:** every **5 unique referred clinics** = **1 free month**
  added to the referrer's next billing cycle. Capped at
  `MAX_REFERRAL_CREDITS_PER_YEAR` per year. Reward kind: `CREDIT_MONTHS`.
- **Commercial/lab/medical-shop partner → Facility:** the proposed current
  contract is **10% of net collected subscription revenue for the first 12
  billed months**, subject to the authoritative Docita plan and finance/legal
  approval. The current Docita implementation is still release-blocked and
  its old 5%/15%-5% defaults must not be advertised.

Clinic peer referrals may use the existing booking route:
`https://app.docita.work/book/{referralCode}`. Partner campaigns use
`DOCITA_PARTNER_JOIN_URL` (default `https://app.docita.work/partner`) and do
not manufacture `PH-*` or `DG-*` booking codes. The Docita public partner
signup/profile flow is not complete yet; until it is released, use reply JOIN /
REFER for manual onboarding or point the environment variable at an approved
landing page.

The runner preserves valid clinic peer-referral codes only when a peer-referral
template explicitly contains `{{ref_link}}`. Partner and marketing messages
never imply that a cold lead already has a Docita partner account.

For clinics referring clinics, the SMS uses the auto-generated
`PRO-XXXXXXXX` code (from `referrer-code-backfill.service.ts`) if present,
else falls back to `CL-{slug}`.

### Code format

- Pharmacy: `PH-{name-slug}-{area-slug6}` (e.g. `PH-MEDPLUS-BANJAR`).
- Diagnostic: `DG-{name-slug}-{area-slug6}`.
- Clinic: `PRO-XXXXXXXX` (if already in CSV) or `CL-{name-slug}`.

`referral_codes.csv` is generated by `make referral-codes` for clinic peer
referrals. The runner reads it so the same code is used across re-sends. It is
not a substitute for the Docita partner attribution ledger.

---

## 5. Templates (final copy)

Stored in `templates.yaml`. All variants target at most 2 SMS segments and
include an opt-out instruction. Partner, pharmacy, diagnostic, and marketing
templates use `{{partner_join_url}}`, resolved from `DOCITA_PARTNER_JOIN_URL`.

### 5.1 Clinics & hospitals — `docita_hms_sms_v1` (primary, "try Docita" pitch)

```
Hi {{name}}, running {{area}} clinic on paper/Excel/WhatsApp? Docita runs appointments, billing, EMR, Rx, invoices & follow-ups in one workspace. 15-min demo, no migration. Reply DEMO. {{phone}} | {{web}} Reply STOP to opt out.
```

**No referral ask on first contact.** Clinics get the "try Docita" pitch only.
The peer-referral template (5.3 below) is reserved for **after a clinic
onboards** — not the cold-outreach blast. The runner refuses if you try to
send `docita_clinic_referral_v1` against the clinic campaign.

### 5.2 Clinics & hospitals — `docita_hms_sms_v2` (shorter, higher reply rate)

```
Hi {{name}}, are you using any clinic software today?
Docita handles appointments, billing, EMR, Rx & WhatsApp reminders.
Reply YES (already using) or DEMO. {{phone}} | {{web}} Reply STOP to opt out.
```

### 5.3 Clinics & hospitals — `docita_clinic_referral_v1` (peer referral, **post-onboarding only**)

```
Hi {{name}}, refer a clinic to Docita — for every 5 that go live,
you get 1 month free on your next billing. We handle demo + setup.
Your ref link: {{ref_link}} {{phone}} | {{web}} Reply STOP to opt out.
```

This template is only for existing Docita customers, not cold leads. The
runner enforces this. To send it to onboarded customers, build a separate
`onboarded_clinics.csv` and run the runner against that file with
`--campaign clinic --template docita_clinic_referral_v1 --allow-clinic-referral
--confirm`.

### 5.4 Pharmacies — `docita_pharmacy_sms_v1` (referral partner)

```
Hi {{name}}, earn 10% of net collected Docita subscription revenue for a referred facility's first 12 billed months. Join: {{partner_join_url}} or reply JOIN. {{phone}} | {{web}} Reply STOP to opt out.
```

### 5.5 Pharmacies — `docita_pharmacy_sms_v2` (softer)

```
Hi {{name}}, Docita helps clinics in {{area}} go digital. Refer one and earn 10% of net collected subscription revenue for its first 12 billed months. Join: {{partner_join_url}} or reply REFER. {{phone}} Reply STOP to opt out.
```

### 5.6 Diagnostics — `docita_diagnostics_sms_v1`

```
Hi {{name}}, Docita onboards clinics/hospitals in {{area}}. Know one that needs appointments, billing & EMR? Connect us for 10% of net collected revenue for its first 12 billed months. Join: {{partner_join_url}} or reply REFER. Reply STOP to opt out.
```

### 5.7 Partner and marketing campaigns — `docita_partner_sms_v1` / `docita_marketing_sms_v1`

Use the templates in `templates.yaml` for explicitly approved partner lists.
They do not claim that a partner account already exists and do not use a
synthetic booking/referral code.

### 5.8 Generic intro (any audience) — `docita_intro_sms_v1`

```
Hi {{name}}, Docita — clinic & hospital software for India.
Appointments, EMR, Rx, billing, WhatsApp reminders in one workspace.
Free 15-min demo? Reply DEMO. {{phone}} | {{web}} Reply STOP to opt out.
```

### 5.9 Test-only — `docita_test_sms_v1`

```
Docita SMS test from {{from}}. If you got this, the gateway works.
Time: {{ts}}. Reply STOP to opt out.
```

---

## 6. Sending rules (carriers & people)

- **Throttle:** 4 seconds between messages (15 msg/min). Stays well under
  all Indian carrier limits. Configurable via `make SMS_DELAY=2`.
- **Daily cap:** 500 messages / day / device, by default. Lift by editing
  `LIMIT=...` in the make invocation.
- **Time window:** Mon–Fri 10:00–12:30 IST and 15:00–18:00 IST, Sat
  10:00–13:00 IST. Outside these, the runner refuses unless
  `ALLOW_OFFHOURS=1` is set.
- **STOP handling:** every live template ends with `Reply STOP to opt out.`
  `make sms-inbox-once` adds STOP replies to `do_not_sms.csv` and the runner
  suppresses that normalized number across campaigns.
- **Durable attempts:** the runner writes the lead CSV after every attempt and
  appends `sms_delivery_log.csv`. A `2xx` response means
  `gateway_accepted`, not carrier-delivered; the local phone gateway does not
  expose a carrier delivery receipt.
- **Per-campaign dedupe:** an accepted row is skipped only for the same
  campaign. `--force` is an explicit resend and also bypasses the time window.
- **Daily cap:** `LIMIT` remains a per-run cap. Review carrier/SIM terms before
  increasing it; this local setup is not an approved high-volume marketing
  provider.

---

## 7. Commands (the actual `make` interface)

All commands live in `Makefile`. Run `make help` for the live list.

### Setup (run once)

```bash
cd hospital-scraper
cp .env.example .env       # then fill SMS_GATEWAY_URL + USER + PASS + OWNER_PHONE_E164
make install               # pip install -r requirements.txt
make doctor                # pings the phone — confirm before any real send
```

### Per-audience dry-runs (safe, no SMS sent)

```bash
make sms-preview-clinics       # first 10 clinic messages
make sms-preview-pharmacies    # first 10 pharmacy messages
make sms-preview-diagnostics   # first 10 diagnostic messages
make sms-preview-partners      # first 10 approved partner messages
make sms-preview-marketing     # first 10 approved marketing messages
make sms-preview-all           # combined
```

### Per-audience live sends (with confirmation prompt in the runner)

```bash
make sms-clinics               # T1 clinics, default 500 cap
make sms-pharmacies            # T1 pharmacies
make sms-diagnostics           # T1 diagnostics
make sms-partners              # approved partner list (set CSV_PARTNER=...)
make sms-marketing             # approved marketing list (set CSV_MARKETING=...)
make test-doctor               # 1 SMS to your own number (OWNER_PHONE_E164)
```

### Force-send (no prompt — for re-runs / re-sends)

```bash
make sms-clinics-force
make sms-pharmacies-force
make sms-diagnostics-force
make sms-partners-force
make sms-marketing-force
```

### Limit / override knobs

```bash
make sms-clinics LIMIT=50
make sms-clinics TEMPLATE_CLINIC=docita_hms_sms_v2
make sms-clinics ALLOW_OFFHOURS=1 SMS_DELAY=2
```

### Operational

```bash
make doctor                # quick reachability ping
make doctor-help           # print a checklist for common issues
make sms-status            # how many sent / failed per campaign
make sms-delivery-status   # gateway_accepted vs failed attempts
make enrich-contacts       # public official-site phone enrichment
make referral-codes        # (re)build referral_codes.csv from lead CSVs
make sms-suppress-list     # (re)build empty do_not_sms.csv template
```

---

## 8. Files in this repo

| File | Purpose |
|---|---|
| `Makefile` | All `make` targets. Single source of truth. |
| `scripts/sms_send.py` | Orchestrator: reads CSV, renders template, records durable attempts, calls `LocalSmsGateway`. |
| `scripts/sms_test.py` | Sends the explicit one-message `OWNER_PHONE_E164` gateway smoke; no lead CSV is touched. |
| `scripts/enrich_contacts.py` | Bounded public-site enrichment; adds `phone_numbers`, source URLs, and status. |
| `contact_enrichment.py` | Robots-aware extractor for official website pages only. |
| `scripts/build_referral_codes.py` | Builds `referral_codes.csv` (phone → referralCode, ref_link). |
| `sms_gateway.py` | `LocalSmsGateway` — HTTP client for the phone's local SMS server. |
| `templates.yaml` | Final copy. |
| `outreach_common.py` | Shared helpers (phone normalization, tracking columns, render, CSV I/O). |
| `whatsapp_outreach.py` | Pre-existing — generates wa.me links (manual, no API). |
| `.env.example` | `SMS_GATEWAY_URL/USER/PASS`, `OWNER_PHONE_E164`, `DOCITA_PARTNER_JOIN_URL`. |
| `docs/SMS_OUTREACH_PLAN.md` | This doc. |
| `do_not_sms.csv` | Generated suppression list. |
| `referral_codes.csv` | Generated phone → ref link table. |
| `sms_delivery_log.csv` | Append-only local attempt log; gateway acceptance is not carrier delivery. |
| `leads_*.csv` | Gets `sms_status` etc. columns written back in place. |

---

## 9. Tracking & follow-up

For every send we write back to the lead CSV:

- `sms_status` — `accepted` / `failed` (legacy `sent` is still recognized)
- `sms_campaign` — campaign-specific dedupe key
- `sms_template_key` — which template was used
- `sms_sent_at` — UTC ISO timestamp when the gateway accepted the request
- `sms_last_attempt_at` — UTC ISO timestamp for the latest attempt
- `sms_attempts` — number of attempts for this CSV row
- `sms_gateway_status` — `accepted` / `rejected`
- `sms_delivery_status` — `gateway_accepted` / `failed`; no carrier receipt is available
- `sms_last_message` — exact text sent (for audit + re-render)
- `sms_error` — last error string if failed

`sms_delivery_log.csv` stores the lead key, normalized phone, campaign,
template, timestamp, gateway HTTP status, local delivery status, message
length, and a truncated error. It must not contain OTPs, bank details, or
patient data.

Reply handling (manual, for now):

- Watch the Android phone. Any reply → check number against the CSV →
  mark `reply` column → follow up via WhatsApp (see `whatsapp_outreach.py`).
- capcom6 SMS Gateway has a webhook endpoint on the phone
  (`POST /webhooks`), so we *could* wire inbound replies to a local
  listener on the Mac. Skipped for now — manual is fine for the
  volumes we're talking about.

---

## 10. Compliance quick-check

- **No PHI / no patient data** in any message — we only ever address
  the clinic / pharmacy / diagnostic as a business.
- **Every live message ends with `Reply STOP to opt out.`** (DLT-friendly for
  India; we are not DLT-registered because we use a personal SIM
  directly, not a DLT-bound A2P provider — same as personal WhatsApp
  use, not a commercial SMS blast).
- **T1 filter + business-hours-only** applies to scraped clinic, pharmacy, and
  diagnostic lists. Explicit partner/marketing CSVs use `--all-leads`, but
  still require a valid mobile number, suppression check, approved list, and
  the same send window.
- **Public contact enrichment only:** `make enrich-contacts` fetches bounded
  pages from each lead's official website, respects robots.txt, records source
  URLs, preserves the existing primary phone, and never scrapes Google Maps
  HTML or guesses private numbers. The SMS runner falls back to the first
  normalized `phone_numbers` value when the primary `phone` field is empty.
- **Gateway status is not delivery proof:** a local HTTP `2xx` means only that
  the Android app accepted the request; carrier delivery receipts are not
  available in this architecture.
- **No medical outcome claims.** All templates are about software
  features and a 15-min demo. Matches the trust language in
  `docita/docs/product/DOCITA_MARKETING_DISTRIBUTION_STRATEGY.md §13`.

---

## 11. Out of scope (deferred)

- Multi-phone rotation (need > 1 Android phone + > 1 unlimited SIM to need this).
- Inbound webhook for STOP / replies (waiting on a quiet hour to wire it).
- A/B test of v1 vs v2 automatically (run both, compare `sms_status`
  and `reply` columns over a week, pick the winner).
- DLT registration + commercial A2P route (only if you ever outgrow
  the ~200 msg/hour/SIM ceiling or want to run ads at scale).

---

## 12. TL;DR — first thing to run

```bash
cd /Users/vamsikrishnachandaluri/repos/hospital-scraper
cp .env.example .env
# edit .env: SMS_GATEWAY_URL=http://<phone-ip>:8080, USER, PASS, OWNER_PHONE_E164
# set DOCITA_PARTNER_JOIN_URL to an approved partner landing/signup URL
make install
make doctor                # ping the phone — should print OK
make test-doctor           # 1 SMS to you, proves the pipe
make enrich-contacts       # add public phone numbers before outreach
make sms-preview-clinics   # look at first 10 messages, sanity-check
make sms-clinics           # T1 clinics, default 500 cap
```
