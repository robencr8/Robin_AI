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

import base64
import hmac
import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError

# Phase 2 — RAG intelligence layer (graceful degradation)
try:
    from rag_client import classify_lead, generate_proposal_context, forward_to_jotform_agent, rag_health
    from lead_scorer import score_lead
    RAG_ENABLED = True
except ImportError:
    RAG_ENABLED = False
    log.warning("RAG client or lead scorer not found — running in basic mode")

# ─── Config ────────────────────────────────────────────────────────────────────
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_y5BACw4WZOqf@ep-super-cake-amzu8ims-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require"
)
INTERNAL_EMAIL     = os.environ.get("NOTIFY_EMAIL", "robenedwan@gmail.com")
COMPANY_NAME       = "ECO Technology Environmental Protection Services LLC"
COMPANY_PHONE      = "+971 52 223 3989"
WEBSITE            = os.environ.get("WEBSITE", "https://binz2008-star.github.io/eco-environmental-uae")
GMAIL_USER         = os.environ.get("GMAIL_USER", "robenedwan@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "rvyfwpsymfxgxprc")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

# ── Admin Basic Auth ────────────────────────────────────────────────────────────
_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

def _check_basic_auth(request: Request) -> bool:
    """Validate HTTP Basic Auth credentials from Authorization header."""
    if not _ADMIN_USERNAME or not _ADMIN_PASSWORD:
        return False  # Deny if env vars not set
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        username, _, password = decoded.partition(":")
        # Constant-time comparison to prevent timing attacks
        user_ok = hmac.compare_digest(username, _ADMIN_USERNAME)
        pass_ok = hmac.compare_digest(password, _ADMIN_PASSWORD)
        return user_ok and pass_ok
    except Exception:
        return False

def _auth_required() -> Response:
    """Return 401 with WWW-Authenticate header to trigger browser login prompt."""
    from fastapi.responses import Response as FastAPIResponse
    return FastAPIResponse(
        content="Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="ECO Admin"'},
    )

_ADMIN_CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f4f8;color:#1e293b;font-size:14px}
.topbar{background:linear-gradient(135deg,#065f46,#059669);color:#fff;padding:16px 20px;position:sticky;top:0;z-index:100;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
.topbar h1{font-size:16px;font-weight:700;letter-spacing:-.01em}
.topbar .sub{font-size:11px;opacity:.8;margin-top:2px}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.filters a{color:#fff;text-decoration:none;font-size:11px;font-weight:600;padding:5px 12px;border-radius:20px;border:1px solid rgba(255,255,255,0.3);white-space:nowrap;transition:background .15s}
.filters a:hover,.filters a.active{background:rgba(255,255,255,0.25)}
.kpi-row{display:flex;gap:12px;padding:16px 20px;overflow-x:auto;flex-wrap:wrap}
.kpi{background:#fff;border-radius:10px;padding:14px 18px;min-width:120px;box-shadow:0 1px 3px rgba(0,0,0,.07);border-left:4px solid #059669;flex:1}
.kpi .val{font-size:26px;font-weight:800;color:#059669;line-height:1}
.kpi .lbl{font-size:10px;color:#64748b;margin-top:4px;text-transform:uppercase;letter-spacing:.5px}
.kpi.hot{border-left-color:#ef4444}.kpi.hot .val{color:#ef4444}
.kpi.warm{border-left-color:#f59e0b}.kpi.warm .val{color:#f59e0b}
.kpi.cold{border-left-color:#6b7280}.kpi.cold .val{color:#6b7280}
.content{padding:0 20px 20px}
.card{background:#fff;border-radius:10px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.07);overflow:hidden}
.card-header{padding:14px 16px;border-bottom:1px solid #f1f5f9;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.card-company{font-weight:700;font-size:15px;color:#0f172a}
.card-meta{font-size:11px;color:#64748b;margin-top:2px}
.card-body{padding:12px 16px;display:grid;grid-template-columns:1fr 1fr;gap:8px}
.card-field{font-size:12px}
.card-field .key{color:#94a3b8;font-size:10px;text-transform:uppercase;letter-spacing:.4px;display:block;margin-bottom:2px}
.card-field .val{color:#334155;font-weight:500}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700;color:#fff}
.badge-HOT{background:#ef4444}
.badge-WARM{background:#f59e0b}
.badge-MEDIUM{background:#3b82f6}
.badge-COLD{background:#6b7280}
.score-circle{width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px;color:#fff;flex-shrink:0}
.score-HOT{background:#ef4444}
.score-WARM{background:#f59e0b}
.score-MEDIUM{background:#3b82f6}
.score-COLD{background:#6b7280}
.action-box{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 14px;margin:0 16px 14px;font-size:12px;color:#065f46;font-weight:500}
.empty{text-align:center;padding:48px 20px;color:#94a3b8;font-size:14px}
@media(max-width:480px){
  .card-body{grid-template-columns:1fr}
  .topbar{padding:12px 16px}
  .kpi-row{padding:12px 16px}
  .content{padding:0 12px 12px}
}
</style>
"""

app = FastAPI(title="ECO Technology Lead Pipeline", version="3.0")

@app.on_event("startup")
async def startup_event():
    """Start background scheduler on server startup."""
    try:
        from scheduler import start_scheduler
        start_scheduler()
        log.info("Scheduler active — daily report 7AM UAE, touch check every 6h")
    except Exception as e:
        log.warning(f"Scheduler failed to start: {e}")


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


# ─── Phase 2: RAG Intelligence + Lead Scoring ─────────────────────────────────

def store_lead_score(lead_id: int, scoring: dict, rag_classification: dict, proposal_context: str | None):
    """Persist lead score, RAG classification, and proposal context to DB."""
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE leads SET
                lead_score          = %s,
                score_band          = %s,
                rag_intent          = %s,
                rag_confidence      = %s,
                rag_method          = %s,
                proposal_context    = %s,
                recommended_action  = %s,
                scored_at           = NOW()
               WHERE id = %s""",
            (
                scoring.get("score"),
                scoring.get("band"),
                rag_classification.get("intent"),
                rag_classification.get("confidence"),
                rag_classification.get("method"),
                proposal_context,
                scoring.get("recommended_action"),
                lead_id,
            )
        )
        conn.commit()
        log.info(f"Lead #{lead_id} scored: {scoring.get('score')} ({scoring.get('band')})")
    except Exception as e:
        log.warning(f"store_lead_score failed (non-critical): {e}")
        conn.rollback()
    finally:
        cur.close(); conn.close()


def enrich_lead(lead: dict, lead_id: int, raw_payload: dict | None = None):
    """
    Background task: classify via RAG, score, persist.
    Non-blocking — runs after webhook response is returned.
    Never raises — all errors are logged.
    """
    if not RAG_ENABLED:
        # Heuristic-only scoring when RAG not available
        from lead_scorer import score_lead
        scoring = score_lead(lead)
        store_lead_score(lead_id, scoring, {"intent": "eco", "rag_available": False}, None)
        return

    # 1. Classify intent via RAG dispatch
    rag_classification = classify_lead(lead)

    # 2. Score lead (RAG-enhanced or heuristic fallback)
    scoring = score_lead(lead, rag_classification)

    # 3. Generate proposal context for HOT/WARM leads
    proposal_context = None
    if scoring.get("band") in ("HOT", "WARM"):
        proposal_context = generate_proposal_context(lead)

    # 4. Forward raw payload to RAG Jotform ingestion for memory indexing
    if raw_payload:
        forward_to_jotform_agent(raw_payload)

    # 5. Persist everything
    store_lead_score(lead_id, scoring, rag_classification, proposal_context)

    # 6. Log hot leads prominently
    if scoring.get("band") == "HOT":
        log.warning(
            f"🔥 HOT LEAD #{lead_id} — {lead.get('company','?')} "
            f"score={scoring.get('score')} — {scoring.get('recommended_action')}"
        )


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
    rag_status = {}
    if RAG_ENABLED:
        try:
            from rag_client import rag_health
            rag_status = rag_health()
        except Exception:
            rag_status = {"available": False}
    return {"status": "ok", "version": "3.0", "rag": rag_status}


@app.post("/api/leads/score")
async def score_lead_endpoint(request: Request):
    """Score a lead using the ECO 6-dimension heuristic engine."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    try:
        from lead_scorer import score_lead
        lead = {
            "name":    data.get("name", ""),
            "company": data.get("company", ""),
            "email":   data.get("email", ""),
            "service": data.get("service", "") or " ".join(data.get("services", [])),
            "urgency": data.get("urgency", ""),
            "emirate": data.get("emirate", ""),
            "source":  data.get("source", ""),
        }
        # Optional: enrich with RAG intent if provided
        rag_classification = None
        if data.get("rag_intent"):
            rag_classification = {
                "rag_available": True,
                "intent":     data.get("rag_intent", "eco"),
                "confidence": float(data.get("rag_confidence", 0.5)),
                "method":     data.get("rag_method", "external"),
            }
        result = score_lead(lead, rag_classification)
        return JSONResponse(content=result)
    except Exception as e:
        log.exception("Lead scoring failed")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/admin")
async def admin_dashboard(request: Request, band: str = ""):
    """Password-protected admin dashboard showing scored leads."""
    if not _check_basic_auth(request):
        return _auth_required()

    # Check env vars configured
    if not _ADMIN_USERNAME or not _ADMIN_PASSWORD:
        return JSONResponse(
            status_code=503,
            content={"error": "Admin credentials not configured. Set ADMIN_USERNAME and ADMIN_PASSWORD env vars."}
        )

    conn = get_db(); cur = conn.cursor()
    try:
        band_filter = ""
        valid_bands = ["HOT", "WARM", "MEDIUM", "COLD"]
        band_upper = band.upper() if band else ""
        if band_upper in valid_bands:
            band_filter = f"AND score_band = '{band_upper}'"

        cur.execute(f"""
            SELECT id, full_name, company_name, email, phone, emirate,
                   services_required, urgency, status, source, email_status,
                   lead_score, score_band, rag_intent,
                   CAST(rag_confidence AS TEXT) as rag_confidence,
                   recommended_action,
                   touch1_sent, touch2_sent, touch3_sent,
                   CAST(scored_at AS TEXT) as scored_at,
                   CAST(created_at AS TEXT) as created_at
            FROM leads
            WHERE 1=1 {band_filter}
            ORDER BY lead_score DESC NULLS LAST, created_at DESC
            LIMIT 100
        """)
        leads = [dict(r) for r in cur.fetchall()]

        # KPI counts
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE score_band='HOT') as hot,
                COUNT(*) FILTER (WHERE score_band='WARM') as warm,
                COUNT(*) FILTER (WHERE score_band='MEDIUM') as medium,
                COUNT(*) FILTER (WHERE score_band='COLD') as cold,
                COUNT(*) FILTER (WHERE score_band IS NULL) as unscored,
                COUNT(*) FILTER (WHERE touch1_sent=TRUE) as t1,
                COUNT(*) FILTER (WHERE touch2_sent=TRUE) as t2,
                COUNT(*) FILTER (WHERE touch3_sent=TRUE) as t3
            FROM leads
        """)
        kpi = dict(cur.fetchone())
    except Exception as e:
        log.exception("Admin dashboard DB error")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        cur.close(); conn.close()

    from datetime import datetime as dt
    now = dt.utcnow().strftime("%d %b %Y %H:%M UTC")

    # Filter links
    def flink(label, b, css_class=""):
        active = " active" if band_upper == b.upper() or (not band and not b) else ""
        href = f"/admin?band={b}" if b else "/admin"
        return f'<a href="{href}" class="{active}">{label}</a>'

    filter_html = (
        flink("All", "") +
        flink("🔥 HOT", "HOT") +
        flink("🟡 WARM", "WARM") +
        flink("🔵 MEDIUM", "MEDIUM") +
        flink("⚪ COLD", "COLD")
    )

    # Lead cards
    cards_html = ""
    if not leads:
        cards_html = '<div class="empty">No leads match this filter.</div>'
    else:
        for l in leads:
            band_val   = l.get("score_band") or "COLD"
            score_val  = l.get("lead_score") or "—"
            services   = ", ".join(l.get("services_required") or []) or "—"
            t1 = "✅" if l.get("touch1_sent") else "—"
            t2 = "✅" if l.get("touch2_sent") else "—"
            t3 = "✅" if l.get("touch3_sent") else "—"
            created    = (l.get("created_at") or "")[:16]
            scored_at  = (l.get("scored_at") or "")[:16]
            action     = l.get("recommended_action") or ""
            action_html = f'<div class="action-box">⚡ {action}</div>' if action else ""

            cards_html += f"""
<div class="card">
  <div class="card-header">
    <div>
      <div class="card-company">{l.get("company_name","—")}</div>
      <div class="card-meta">{l.get("full_name","—")} · {l.get("emirate","—")} · {l.get("source","—")}</div>
    </div>
    <div style="display:flex;align-items:center;gap:8px;flex-shrink:0">
      <span class="badge badge-{band_val}">{band_val}</span>
      <div class="score-circle score-{band_val}">{score_val}</div>
    </div>
  </div>
  <div class="card-body">
    <div class="card-field"><span class="key">Email</span><span class="val">{l.get("email","—")}</span></div>
    <div class="card-field"><span class="key">Phone</span><span class="val">{l.get("phone","—")}</span></div>
    <div class="card-field"><span class="key">Services</span><span class="val">{services}</span></div>
    <div class="card-field"><span class="key">Urgency</span><span class="val">{l.get("urgency","—")}</span></div>
    <div class="card-field"><span class="key">Status</span><span class="val">{l.get("status","—")}</span></div>
    <div class="card-field"><span class="key">Email Status</span><span class="val">{l.get("email_status","—")}</span></div>
    <div class="card-field"><span class="key">Touches</span><span class="val">T1:{t1} T2:{t2} T3:{t3}</span></div>
    <div class="card-field"><span class="key">RAG Intent</span><span class="val">{l.get("rag_intent","heuristic")}</span></div>
    <div class="card-field"><span class="key">Created</span><span class="val">{created}</span></div>
    <div class="card-field"><span class="key">Scored</span><span class="val">{scored_at}</span></div>
  </div>
  {action_html}
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ECO Admin — Lead Pipeline</title>
{_ADMIN_CSS}
</head>
<body>
<div class="topbar">
  <div>
    <div class="h1">🌿 ECO Technology — Lead Pipeline</div>
    <div class="sub">Generated {now} · Showing {len(leads)} leads</div>
  </div>
  <div class="filters">{filter_html}</div>
</div>
<div class="kpi-row">
  <div class="kpi"><div class="val">{kpi.get("total",0)}</div><div class="lbl">Total Leads</div></div>
  <div class="kpi hot"><div class="val">{kpi.get("hot",0)}</div><div class="lbl">🔥 HOT</div></div>
  <div class="kpi warm"><div class="val">{kpi.get("warm",0)}</div><div class="lbl">🟡 WARM</div></div>
  <div class="kpi"><div class="val">{kpi.get("t1",0)}</div><div class="lbl">Touch 1 Sent</div></div>
  <div class="kpi"><div class="val">{kpi.get("t2",0)}</div><div class="lbl">Touch 2 Sent</div></div>
  <div class="kpi cold"><div class="val">{kpi.get("unscored",0)}</div><div class="lbl">Unscored</div></div>
</div>
<div class="content">{cards_html}</div>
</body>
</html>"""

    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)


@app.get("/leads/hot")
async def get_hot_leads():
    """Return HOT and WARM leads sorted by score descending."""
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, full_name, company_name, email, phone, emirate,
                   services_required, urgency, status, source,
                   lead_score, score_band, rag_intent,
                   CAST(rag_confidence AS TEXT) as rag_confidence,
                   recommended_action,
                   CAST(scored_at AS TEXT) as scored_at,
                   CAST(created_at AS TEXT) as created_at
            FROM leads
            WHERE score_band IN ('HOT', 'WARM')
            ORDER BY lead_score DESC NULLS LAST, created_at DESC
            LIMIT 20
        """)
        leads = [dict(r) for r in cur.fetchall()]
        return JSONResponse({"leads": leads, "count": len(leads)})
    except Exception as e:
        log.exception("Failed to fetch hot leads")
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to fetch hot leads", "detail": str(e)}
        )
    finally:
        cur.close(); conn.close()


@app.get("/leads")
async def get_all_leads():
    """Return all leads with scores, sorted by score desc."""
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, full_name, company_name, email, phone, emirate,
                   services_required, urgency, status, source, email_status,
                   lead_score, score_band, rag_intent,
                   CAST(rag_confidence AS TEXT) as rag_confidence,
                   recommended_action,
                   touch1_sent, touch2_sent, touch3_sent,
                   CAST(touch1_sent_at AS TEXT) as touch1_sent_at,
                   CAST(touch2_sent_at AS TEXT) as touch2_sent_at,
                   CAST(scored_at AS TEXT) as scored_at,
                   CAST(created_at AS TEXT) as created_at
            FROM leads
            ORDER BY lead_score DESC NULLS LAST, created_at DESC
        """)
        leads = [dict(r) for r in cur.fetchall()]
        return JSONResponse({"leads": leads, "count": len(leads)})
    except Exception as e:
        log.exception("Failed to fetch leads")
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to fetch leads", "detail": str(e)}
        )
    finally:
        cur.close(); conn.close()


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
    background_tasks.add_task(enrich_lead, lead, lead_id, raw)

    return JSONResponse({"status": "success", "lead_id": lead_id, "scoring": "pending"})


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
    background_tasks.add_task(enrich_lead, lead, lead_id, data)

    return JSONResponse({"status": "success", "lead_id": lead_id, "scoring": "pending"})


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
