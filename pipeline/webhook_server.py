"""
ECO Technology — Lead Automation Pipeline v3.0
===============================================
Production-grade FastAPI webhook server.

Fixes applied vs v2.0:
  ✅ Async email dispatch via BackgroundTasks (no blocking)
  ✅ Idempotency: duplicate webhook submissions are silently skipped
  ✅ Retry system: tenacity with 3 attempts + exponential backoff
  ✅ Lead state tracking: email_status column updated after dispatch
  ✅ Event log: every action written to lead_events table
  ✅ Failure logging: failed emails recorded as events, never silently dropped

Endpoints:
  POST /webhook/jotform        — Jotform form submissions
  POST /webhook/agent          — Robin AI agent lead captures
  GET  /leads                  — Quick lead dashboard
  GET  /leads/{id}/events      — Full event timeline for a lead
  GET  /health                 — Health check
"""

import os
import json
import smtplib
import logging
import hashlib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError

# ─── Config ────────────────────────────────────────────────────────────────────
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_y5BACw4WZOqf@ep-super-cake-amzu8ims-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require"
)
INTERNAL_EMAIL     = os.environ.get("NOTIFY_EMAIL", "robenedwan@gmail.com")
COMPANY_NAME       = "ECO Technology Environmental Protection Services LLC"
COMPANY_PHONE      = "+971 52 223 3989"
WEBSITE            = os.environ.get("WEBSITE", "https://binz2008-star.github.io/eco-environmental")
GMAIL_USER         = os.environ.get("GMAIL_USER", "robenedwan@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "rvyfwpsymfxgxprc")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

app = FastAPI(title="ECO Technology Lead Pipeline", version="3.0")


# ─── DB helpers ────────────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def log_event(lead_id: int, event_type: str, payload: dict):
    """Write an immutable event record to lead_events."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO lead_events (lead_id, event_type, payload) VALUES (%s, %s, %s)",
            (lead_id, event_type, json.dumps(payload))
        )
        conn.commit()
    except Exception as e:
        log.error(f"log_event failed [{event_type}] lead={lead_id}: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()


def is_duplicate(idempotency_key: str) -> bool:
    """Return True if this idempotency_key has already been processed."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM form_submissions WHERE idempotency_key = %s LIMIT 1",
            (idempotency_key,)
        )
        return cur.fetchone() is not None
    finally:
        cur.close()
        conn.close()


def store_lead(data: dict) -> int:
    """Upsert lead by email. Returns lead_id."""
    conn = get_db()
    cur = conn.cursor()
    services_arr = [data["service"]] if data.get("service") else []
    try:
        cur.execute("SELECT id FROM leads WHERE email = %s", (data.get("email"),))
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """UPDATE leads SET
                       full_name         = COALESCE(%s, full_name),
                       company_name      = COALESCE(%s, company_name),
                       phone             = COALESCE(%s, phone),
                       services_required = COALESCE(%s, services_required),
                       source            = COALESCE(%s, source),
                       updated_at        = NOW()
                   WHERE id = %s RETURNING id""",
                (data.get("name"), data.get("company"), data.get("phone"),
                 services_arr or None, data.get("source", "website_form"),
                 existing["id"])
            )
            lead_id = cur.fetchone()["id"]
        else:
            cur.execute(
                """INSERT INTO leads
                       (full_name, company_name, email, phone,
                        services_required, source, status, email_status,
                        created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, 'new', 'pending', NOW(), NOW())
                   RETURNING id""",
                (data.get("name"), data.get("company"), data.get("email"),
                 data.get("phone"), services_arr, data.get("source", "website_form"))
            )
            lead_id = cur.fetchone()["id"]
        conn.commit()
        return lead_id
    finally:
        cur.close()
        conn.close()


def store_form_submission(data: dict, lead_id: int, idempotency_key: str):
    """Log raw form submission. Silently skips on duplicate key."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO form_submissions
                   (lead_id, form_id, submission_data, idempotency_key, submitted_at)
               VALUES (%s, %s, %s, %s, NOW())
               ON CONFLICT (idempotency_key) DO NOTHING""",
            (lead_id, data.get("form_id", "unknown"),
             json.dumps(data), idempotency_key)
        )
        conn.commit()
    except Exception as e:
        log.error(f"store_form_submission failed: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()


def update_email_status(lead_id: int, status: str):
    """Update leads.email_status for state tracking."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE leads SET email_status = %s WHERE id = %s",
            (status, lead_id)
        )
        conn.commit()
    except Exception as e:
        log.error(f"update_email_status failed: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()


# ─── Email layer ───────────────────────────────────────────────────────────────
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=15),
    reraise=True
)
def _smtp_send(to: str, subject: str, body: str):
    """Low-level SMTP send. Retried up to 3x with exponential backoff."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Robin Edwan — ECO Technology <{GMAIL_USER}>"
    msg["To"]      = to
    msg.attach(MIMEText(body, "plain", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, [to], msg.as_string())


def send_client_thankyou(lead: dict) -> bool:
    name    = lead.get("name", "Valued Client")
    service = lead.get("service", "your requested service")
    email   = lead.get("email")
    if not email:
        return False
    subject = "شكراً لتواصلك مع ECO Technology | Thank You for Contacting Us"
    body = f"""Dear {name},

Thank you for reaching out to ECO Technology Environmental Protection Services LLC.
We have received your enquiry regarding: {service}

Our team will review your request and contact you within 2 business hours to discuss your requirements and arrange a free site visit.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
What happens next:
  1. Our specialist reviews your request
  2. We contact you within 2 hours to confirm details
  3. Free site visit arranged at your convenience
  4. Detailed quotation provided within 24 hours
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For urgent matters (overflow, blockage, emergency):
📞 Call us directly: {COMPANY_PHONE}
🌐 {WEBSITE}

Best regards,
Robin Edwan — General Manager
{COMPANY_NAME}
📞 {COMPANY_PHONE} · Ajman, UAE · Est. 2016 · ISO 14001 · ISO 9001
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
عزيزي {name}،
شكراً لتواصلك مع شركة ECO Technology لخدمات الحماية البيئية.
لقد استلمنا طلبك وسيتواصل معك فريقنا خلال ساعتين.
للحالات الطارئة: {COMPANY_PHONE}
"""
    try:
        _smtp_send(email, subject, body)
        return True
    except RetryError as e:
        log.error(f"Client thank-you FAILED after 3 retries → {email}: {e}")
        return False


def send_internal_alert(lead: dict, lead_id: int, source: str) -> bool:
    name    = lead.get("name", "Unknown")
    company = lead.get("company", "Not provided")
    email   = lead.get("email", "Not provided")
    phone   = lead.get("phone", "Not provided")
    service = lead.get("service", "Not specified")
    ts      = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    subject = f"🔔 New Lead #{lead_id} — {name} ({company}) via {source}"
    body = f"""NEW LEAD CAPTURED — ECO Technology Pipeline
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Lead ID   : #{lead_id}
Source    : {source}
Received  : {ts}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Name      : {name}
Company   : {company}
Email     : {email}
Phone     : {phone}
Service   : {service}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACTION: Contact {name} within 2 hours
→ Reply: {email}  |  Call: {phone}
"""
    try:
        _smtp_send(INTERNAL_EMAIL, subject, body)
        return True
    except RetryError as e:
        log.error(f"Internal alert FAILED after 3 retries → lead #{lead_id}: {e}")
        return False


# ─── Background task (runs AFTER response is returned to caller) ───────────────
def dispatch_emails(lead: dict, lead_id: int, source: str):
    """
    Async background task. Sends both emails, updates lead state, logs events.
    This function is NEVER called inside the request thread.
    """
    update_email_status(lead_id, "sending")

    client_sent   = send_client_thankyou(lead)
    internal_sent = send_internal_alert(lead, lead_id, source)

    if client_sent and internal_sent:
        final_status = "sent"
    elif client_sent or internal_sent:
        final_status = "partial"
    else:
        final_status = "failed"

    update_email_status(lead_id, final_status)

    log_event(lead_id, "email_client_thankyou", {
        "success": client_sent, "to": lead.get("email"), "source": source
    })
    log_event(lead_id, "email_internal_alert", {
        "success": internal_sent, "to": INTERNAL_EMAIL, "source": source
    })

    if final_status == "failed":
        log.error(f"Both emails failed for lead #{lead_id} — manual follow-up required")


# ─── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.0"}


@app.post("/webhook/jotform")
async def jotform_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receives Jotform form submissions."""
    try:
        raw = await request.json()
    except Exception:
        form = await request.form()
        raw = dict(form)

    # Idempotency key — prefer Jotform's submission ID, fall back to payload hash
    submission_id = raw.get("submissionID") or raw.get("submission_id") or raw.get("formID")
    idempotency_key = submission_id or hashlib.sha256(
        json.dumps(raw, sort_keys=True).encode()
    ).hexdigest()

    if is_duplicate(idempotency_key):
        log.info(f"Duplicate Jotform submission skipped: {idempotency_key}")
        return JSONResponse({"status": "skipped", "reason": "duplicate"}, status_code=200)

    lead: dict = {}
    for k, val in raw.items():
        k_lower = k.lower()
        if any(x in k_lower for x in ["name", "fullname", "full_name"]):
            lead["name"] = str(val).strip()
        elif any(x in k_lower for x in ["email", "mail"]):
            lead["email"] = str(val).strip().lower()
        elif any(x in k_lower for x in ["phone", "mobile", "tel"]):
            lead["phone"] = str(val).strip()
        elif any(x in k_lower for x in ["company", "organization", "business"]):
            lead["company"] = str(val).strip()
        elif any(x in k_lower for x in ["service", "request", "type"]):
            lead["service"] = str(val).strip()

    lead["source"]  = "jotform_form"
    lead["form_id"] = raw.get("formID", raw.get("form_id", "unknown"))

    if not lead.get("email") and not lead.get("phone"):
        return JSONResponse({"status": "skipped", "reason": "no contact info"}, status_code=200)

    # DB write first — always before any side effects
    lead_id = store_lead(lead)
    store_form_submission({**lead, **raw}, lead_id, idempotency_key)
    log_event(lead_id, "form_submitted", {"source": "jotform", "form_id": lead["form_id"]})

    # Respond immediately — emails run in background
    background_tasks.add_task(dispatch_emails, lead, lead_id, "Jotform Form Submission")

    return JSONResponse({"status": "success", "lead_id": lead_id})


@app.post("/webhook/agent")
async def agent_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receives Robin AI agent lead capture data."""
    try:
        data = await request.json()
    except Exception:
        form = await request.form()
        data = dict(form)

    lead = {
        "name":       data.get("name") or data.get("visitor_name"),
        "company":    data.get("company") or data.get("visitor_company"),
        "email":      data.get("email") or data.get("visitor_email"),
        "phone":      data.get("phone") or data.get("visitor_phone"),
        "service":    data.get("service") or data.get("services_enquired"),
        "source":     "robin_ai_agent",
        "session_id": data.get("session_id"),
        "summary":    data.get("conversation_summary", ""),
    }

    if not lead.get("email") and not lead.get("phone"):
        return JSONResponse({"status": "skipped", "reason": "no contact info"}, status_code=200)

    lead_id = store_lead(lead)

    if lead.get("session_id"):
        conn = get_db()
        cur  = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO agent_conversations
                       (session_id, visitor_name, visitor_company, visitor_email,
                        visitor_phone, services_enquired, conversation_summary,
                        lead_captured, lead_id, jotform_agent_id, started_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s,
                           '019dbd271be77876b7e36ce06ac88fd491ef', NOW())
                   ON CONFLICT (session_id) DO UPDATE SET
                       lead_captured        = true,
                       lead_id              = EXCLUDED.lead_id,
                       conversation_summary = EXCLUDED.conversation_summary""",
                (lead["session_id"], lead["name"], lead["company"],
                 lead["email"], lead["phone"],
                 [lead["service"]] if lead.get("service") else [],
                 lead.get("summary", ""), lead_id)
            )
            conn.commit()
        except Exception as e:
            log.error(f"agent_conversations insert failed: {e}")
            conn.rollback()
        finally:
            cur.close()
            conn.close()

    log_event(lead_id, "agent_interaction", {
        "session_id": lead.get("session_id"),
        "services":   lead.get("service"),
        "summary":    lead.get("summary", "")[:200]
    })

    background_tasks.add_task(dispatch_emails, lead, lead_id, "Robin AI Agent")

    return JSONResponse({"status": "success", "lead_id": lead_id})


@app.get("/leads")
async def get_leads(status: Optional[str] = None, limit: int = 50):
    """Quick lead dashboard endpoint."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        if status:
            cur.execute(
                "SELECT * FROM leads WHERE status = %s ORDER BY created_at DESC LIMIT %s",
                (status, limit)
            )
        else:
            cur.execute(
                "SELECT * FROM leads ORDER BY created_at DESC LIMIT %s", (limit,)
            )
        leads = cur.fetchall()
        return JSONResponse({"leads": [dict(r) for r in leads], "count": len(leads)})
    finally:
        cur.close()
        conn.close()


@app.get("/leads/{lead_id}/events")
async def get_lead_events(lead_id: int):
    """Return full event timeline for a lead."""
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM lead_events WHERE lead_id = %s ORDER BY created_at ASC",
            (lead_id,)
        )
        events = cur.fetchall()
        return JSONResponse({"lead_id": lead_id, "events": [dict(e) for e in events]})
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webhook_server:app", host="0.0.0.0", port=8080, reload=False)
