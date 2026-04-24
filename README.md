# Robin AI — ECO Technology Lead Automation Pipeline

**Production-grade AI sales agent + automated lead pipeline for ECO Technology Environmental Protection Services LLC.**

---

## System Overview

```
Client visits website / Instagram / WhatsApp
        ↓
Robin AI Agent (Jotform) — answers questions, captures lead
        ↓
Webhook Server (FastAPI) — receives lead, stores in DB, sends emails
        ↓
Neon PostgreSQL — lead stored with status, source, session ID
        ↓
Dual email notification:
  → Client: thank-you + confirmation (bilingual AR/EN)
  → Robin Edwan: internal alert with full lead details
        ↓
Daily Report (Python) — sent every morning to robenedwan@gmail.com
        ↓
Close CRM — outreach sequences (Touch 1 → 2 → 3)
```

---

## Repository Structure

```
Robin_AI/
├── agent/
│   └── robin_agent_system_prompt_v6.md   # Production system prompt (paste into Jotform)
├── pipeline/
│   ├── webhook_server.py                  # FastAPI webhook server (main backend)
│   ├── daily_report.py                    # Daily intelligence report generator
│   └── send_report_email.py               # Email sender utility
└── README.md
```

---

## Components

### 1. Robin AI Agent (Jotform)
- **Agent ID:** `019dbd271be77876b7e36ce06ac88fd491ef`
- **App URL:** https://www.jotform.com/app/261127959395470
- **Embed:** `<script src='https://cdn.jotfor.ms/agent/embedjs/019dbd271be77876b7e36ce06ac88fd491ef/embed.js'></script>`
- **System Prompt:** `agent/robin_agent_system_prompt_v6.md`

**Architecture:** Decision-first, identity-second. 15-class intent detection. Strict behavioral rules. Robin's voice applied last.

### 2. Webhook Server (FastAPI)
- **File:** `pipeline/webhook_server.py`
- **Port:** 8080
- **Endpoints:**
  - `GET /health` — health check
  - `POST /webhook/agent` — Robin AI agent lead capture
  - `POST /webhook/jotform` — Jotform form submission
  - `GET /leads` — list all leads (internal)

**Start server:**
```bash
python3.11 pipeline/webhook_server.py
```

### 3. Database (Neon PostgreSQL)
- **Project:** eco-technology-leads
- **Tables:** `leads`, `outreach_log`, `agent_conversations`, `social_posts`, `form_submissions`
- **Key columns added:** `agent_session_id`, `close_contact_id`, `source`

### 4. Daily Report
- **File:** `pipeline/daily_report.py`
- **Runs:** Every morning (scheduled via Manus)
- **Delivers:** HTML report to robenedwan@gmail.com
- **Sections:** Lead summary, pipeline status, Robin AI stats, action items, Instagram schedule

---

## Email Setup (Required for SMTP)

The webhook server sends emails via Gmail SMTP. To enable:

1. Go to [Google Account → Security → App Passwords](https://myaccount.google.com/apppasswords)
2. Create an App Password for "Mail"
3. Set environment variable:
```bash
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
```

Or add to `.env` file (never commit this).

---

## Close CRM Templates

All three outreach templates updated with:
- Website: `https://binz2008-star.github.io/eco-environmental`
- Touch 1: Cold outreach with website link
- Touch 2: Follow-up with website link
- Touch 3: Final outreach with correct Jotform link

---

## Robin AI Agent — Intent Classes

| Intent | Trigger | Response Strategy |
|---|---|---|
| INT-01 PRICE_ENQUIRY | cost, rate, how much | Anchor high → path to quote |
| INT-02 SERVICE_ENQUIRY | grease trap, sewage, jetting | Service detail → compliance → CTA |
| INT-03 COMPLIANCE_CONCERN | fine, inspection, municipality | Validate → position ECO → urgency |
| INT-04A EMERGENCY_CRITICAL | overflow, flooding, health risk | Phone number first, zero delay |
| INT-04B EMERGENCY_HIGH | blockage, bad smell | Fast response + qualification |
| INT-05 COMPARISON | other companies, competitors | Fact table: certs, track record |
| INT-06 OBJECTION_PRICE | too expensive, cheaper | Fine cost vs. AMC cost reframe |
| INT-07 OBJECTION_TRUST | how do I know, reliable? | Lead with verifiable credentials |
| INT-08 LEAD_READY | send quote, book, I'm interested | Zero friction → capture 4 fields |
| INT-09 GENERAL_INFO | anything else | Answer → Basis → Source |
| INT-10 OUT_OF_SCOPE | unrelated question | Redirect cleanly |
| INT-11 GREETING | hello, hi, مرحبا | Warm → immediately purposeful |
| INT-12 ARABIC_INPUT | Arabic text | Full Arabic mode |
| INT-13 FOLLOW_UP | continuing prior topic | Reference context → move forward |
| INT-14 OBJECTION_TIMING | not now, maybe later | Plant urgency seed |
| INT-15 COMPLAINT | dissatisfaction | Acknowledge → escalate |
| INT-16 QUALIFIED_INTEREST | How soon can you come? | Move directly to close |
| INT-17 REPEAT_VISITOR | references prior context | Never make them repeat |
| INT-18 DOCUMENTATION_REQUEST | certificates, reports | Full documentation list |

---

## Contact

**Robin Edwan** — General Manager, ECO Technology Environmental Protection Services LLC  
📞 +971 52 223 3989  
🌐 https://binz2008-star.github.io/eco-environmental  
📧 robenedwan@gmail.com  
📍 Ajman, UAE · Est. 2016 · ISO 14001 · ISO 9001 · Ajman Municipality Approved
