"""
ECO Technology — Lead Automation Pipeline
==========================================
FastAPI webhook server that handles:
  1. Jotform form submissions (POST /webhook/jotform)
  2. Robin AI agent lead captures (POST /webhook/agent)
  3. Stores leads in Neon DB with status tracking
  4. Sends thank-you email to client
  5. Sends internal lead alert to robenedwan@gmail.com
"""

import os
import json
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional

# ─── Config ────────────────────────────────────────────────────────────────────
DB_URL = os.environ.get("DATABASE_URL", "postgresql://neondb_owner:npg_y5BACw4WZOqf@ep-super-cake-amzu8ims-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require")
INTERNAL_EMAIL = os.environ.get("NOTIFY_EMAIL", "robenedwan@gmail.com")
COMPANY_NAME = "ECO Technology Environmental Protection Services LLC"
COMPANY_PHONE = "+971 52 223 3989"
WEBSITE = os.environ.get("WEBSITE", "https://binz2008-star.github.io/eco-environmental")

# Gmail SMTP config (App Password)
GMAIL_USER = os.environ.get("GMAIL_USER", "robenedwan@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "rvyfwpsymfxgxprc")

log_file = "/tmp/webhook.log" if os.path.exists("/tmp") else None
logging.basicConfig(level=logging.INFO, filename=log_file,
                    format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="ECO Technology Lead Pipeline", version="2.0")


# ─── DB helpers ────────────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def store_lead(data: dict) -> int:
    """Insert or update lead in Neon DB. Returns lead ID.
    
    Actual leads table columns:
    id, full_name, company_name, job_title, email, phone,
    property_type, emirate, services_required (array), urgency,
    num_units, notes, source, status, created_at, updated_at,
    touch1_sent, touch1_sent_at, touch2_sent, touch2_sent_at, ...
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        # Check if lead exists by email
        cur.execute("SELECT id FROM leads WHERE email = %s", (data.get("email"),))
        existing = cur.fetchone()
        
        # Build services array
        service = data.get("service")
        services_arr = [service] if service else []
        
        if existing:
            cur.execute("""
                UPDATE leads SET
                    full_name = COALESCE(%s, full_name),
                    company_name = COALESCE(%s, company_name),
                    phone = COALESCE(%s, phone),
                    services_required = COALESCE(%s, services_required),
                    source = COALESCE(%s, source),
                    updated_at = NOW()
                WHERE id = %s RETURNING id
            """, (
                data.get("name"), data.get("company"), data.get("phone"),
                services_arr if services_arr else None,
                data.get("source", "website_form"),
                existing["id"]
            ))
            lead_id = cur.fetchone()["id"]
        else:
            cur.execute("""
                INSERT INTO leads (full_name, company_name, email, phone,
                                   services_required, source, status,
                                   created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'new', NOW(), NOW())
                RETURNING id
            """, (
                data.get("name"), data.get("company"), data.get("email"),
                data.get("phone"), services_arr,
                data.get("source", "website_form")
            ))
            lead_id = cur.fetchone()["id"]
        conn.commit()
        return lead_id
    finally:
        cur.close()
        conn.close()


def store_form_submission(data: dict, lead_id: int):
    """Log raw form submission data."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO form_submissions (lead_id, form_id, submission_data, submitted_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT DO NOTHING
        """, (lead_id, data.get("form_id", "unknown"), json.dumps(data)))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        cur.close()
        conn.close()


# ─── Email helpers (SMTP via Gmail App Password) ──────────────────────────────
def send_email_via_smtp(to: str, subject: str, body: str) -> bool:
    """Send email via Gmail SMTP using App Password."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Robin Edwan — ECO Technology <{GMAIL_USER}>"
        msg["To"] = to
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, [to], msg.as_string())
        logging.info(f"Email sent to {to}: {subject}")
        return True
    except Exception as e:
        logging.error(f"Email failed to {to}: {e}")
        return False

# Alias for backward compatibility
send_email_via_mcp = send_email_via_smtp


def send_client_thankyou(lead: dict):
    """Send thank-you confirmation email to the client."""
    name = lead.get("name", "Valued Client")
    service = lead.get("service", "your requested service")
    email = lead.get("email")
    if not email:
        return False

    subject = f"شكراً لتواصلك مع ECO Technology | Thank You for Contacting Us"
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

Learn more about our services:
🌐 {WEBSITE}

Best regards,
Robin Edwan
General Manager
{COMPANY_NAME}
📞 {COMPANY_PHONE}
🌐 {WEBSITE}

Ajman, UAE · Est. 2016 · ISO 14001 · ISO 9001 · Ajman Municipality Approved

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
عزيزي {name}،
شكراً لتواصلك مع شركة ECO Technology لخدمات الحماية البيئية.
لقد استلمنا طلبك وسيتواصل معك فريقنا خلال ساعتين لمناقشة متطلباتك.
للحالات الطارئة: {COMPANY_PHONE}
"""
    return send_email_via_mcp(email, subject, body)


def send_internal_alert(lead: dict, lead_id: int, source: str):
    """Send internal lead notification to robenedwan@gmail.com."""
    name = lead.get("name", "Unknown")
    company = lead.get("company", "Not provided")
    email = lead.get("email", "Not provided")
    phone = lead.get("phone", "Not provided")
    service = lead.get("service", "Not specified")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    subject = f"🔔 New Lead #{lead_id} — {name} | {company} | ECO Technology"
    body = f"""NEW LEAD CAPTURED — ECO Technology Pipeline
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Lead ID:     #{lead_id}
Source:      {source}
Timestamp:   {timestamp}
Status:      NEW — Action Required
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CUSTOMER DETAILS
Name:        {name}
Company:     {company}
Email:       {email}
Phone:       {phone}
Service:     {service}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACTION REQUIRED
→ Contact {name} within 2 hours
→ Reply to: {email}
→ Call: {phone}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
This lead has been stored in the pipeline database.
Status will update automatically when you log outreach.
"""
    return send_email_via_mcp(INTERNAL_EMAIL, subject, body)


# ─── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "ECO Technology Lead Pipeline", "version": "2.0"}


@app.post("/webhook/jotform")
async def jotform_webhook(request: Request):
    """
    Receives Jotform form submission webhook.
    Jotform sends form data as application/x-www-form-urlencoded or JSON.
    """
    content_type = request.headers.get("content-type", "")
    try:
        if "application/json" in content_type:
            raw = await request.json()
        else:
            form = await request.form()
            raw = dict(form)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bad request: {e}")

    # Normalise Jotform field names (they vary by form)
    # Jotform sends fields like: q3_fullName, q4_email, q5_phoneNumber, etc.
    lead = {}
    for key, val in raw.items():
        k = key.lower()
        if any(x in k for x in ["name", "fullname", "full_name"]):
            lead["name"] = str(val).strip()
        elif any(x in k for x in ["email", "mail"]):
            lead["email"] = str(val).strip().lower()
        elif any(x in k for x in ["phone", "mobile", "tel"]):
            lead["phone"] = str(val).strip()
        elif any(x in k for x in ["company", "organization", "business"]):
            lead["company"] = str(val).strip()
        elif any(x in k for x in ["service", "request", "type"]):
            lead["service"] = str(val).strip()

    lead["source"] = "jotform_form"
    lead["form_id"] = raw.get("formID", raw.get("form_id", "unknown"))

    if not lead.get("email") and not lead.get("phone"):
        return JSONResponse({"status": "skipped", "reason": "no contact info"})

    # Store in DB
    lead_id = store_lead(lead)
    store_form_submission({**lead, **raw}, lead_id)

    # Send emails
    client_sent = send_client_thankyou(lead)
    internal_sent = send_internal_alert(lead, lead_id, "Jotform Form Submission")

    return JSONResponse({
        "status": "success",
        "lead_id": lead_id,
        "client_email_sent": client_sent,
        "internal_email_sent": internal_sent
    })


@app.post("/webhook/agent")
async def agent_webhook(request: Request):
    """
    Receives Robin AI agent lead capture data.
    Called when agent collects customer details.
    """
    try:
        data = await request.json()
    except Exception:
        form = await request.form()
        data = dict(form)

    lead = {
        "name": data.get("name") or data.get("visitor_name"),
        "company": data.get("company") or data.get("visitor_company"),
        "email": data.get("email") or data.get("visitor_email"),
        "phone": data.get("phone") or data.get("visitor_phone"),
        "service": data.get("service") or data.get("services_enquired"),
        "source": "robin_ai_agent",
        "session_id": data.get("session_id"),
        "summary": data.get("conversation_summary", "")
    }

    if not lead.get("email") and not lead.get("phone"):
        return JSONResponse({"status": "skipped", "reason": "no contact info"})

    # Store lead
    lead_id = store_lead(lead)

    # Store agent conversation record
    if lead.get("session_id"):
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO agent_conversations
                    (session_id, visitor_name, visitor_company, visitor_email,
                     visitor_phone, services_enquired, conversation_summary,
                     lead_captured, lead_id, jotform_agent_id, started_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s,
                        '019dbd271be77876b7e36ce06ac88fd491ef', NOW())
                ON CONFLICT (session_id) DO UPDATE SET
                    lead_captured = true, lead_id = EXCLUDED.lead_id,
                    conversation_summary = EXCLUDED.conversation_summary
            """, (
                lead["session_id"], lead["name"], lead["company"],
                lead["email"], lead["phone"],
                [lead["service"]] if lead["service"] else [],
                lead.get("summary", ""), lead_id
            ))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            cur.close()
            conn.close()

    # Send emails
    client_sent = send_client_thankyou(lead)
    internal_sent = send_internal_alert(lead, lead_id, "Robin AI Agent")

    return JSONResponse({
        "status": "success",
        "lead_id": lead_id,
        "client_email_sent": client_sent,
        "internal_email_sent": internal_sent
    })


@app.get("/leads")
async def get_leads(status: Optional[str] = None, limit: int = 50):
    """Quick lead dashboard endpoint."""
    conn = get_db()
    cur = conn.cursor()
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webhook_server:app", host="0.0.0.0", port=8080, reload=False)
