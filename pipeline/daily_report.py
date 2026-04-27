#!/usr/bin/env python3
"""
ECO Technology EPS — Daily Intelligence Report v3.0
====================================================
Fully self-contained — no Manus, no external CLI.
Queries Neon DB directly via psycopg2.
Sends HTML report via Gmail SMTP.
Fires Touch 2 / Touch 3 for overdue leads automatically.
Scheduled via scheduler.py at 7:00 AM UAE time daily.
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError

# ── Config ─────────────────────────────────────────────────────────────────────
DB_URL             = os.environ.get("DATABASE_URL", "postgresql://neondb_owner:npg_y5BACw4WZOqf@ep-super-cake-amzu8ims-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require")
GMAIL_USER         = os.environ.get("GMAIL_USER", "robenedwan@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "rvyfwpsymfxgxprc")
RECIPIENT_EMAIL    = os.environ.get("NOTIFY_EMAIL", "robenedwan@gmail.com")
WEBSITE_URL        = os.environ.get("WEBSITE", "https://binz2008-star.github.io/eco-environmental-uae")
AGENT_APP_URL      = "https://agent.jotform.com/019dbd271be77876b7e36ce06ac88fd491ef"
QUOTE_FORM_URL     = "https://form.jotform.com/261008161739051"

now_utc          = datetime.now(timezone.utc)
REPORT_DATE      = now_utc.strftime("%A, %d %B %Y")
REPORT_TIMESTAMP = now_utc.strftime("%Y-%m-%d %H:%M UTC")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── DB ─────────────────────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def db_query(sql, params=None):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute(sql, params or ()); return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.error(f"DB query failed: {e}"); return []
    finally:
        cur.close(); conn.close()

def db_execute(sql, params=None):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute(sql, params or ()); conn.commit(); return True
    except Exception as e:
        log.error(f"DB execute failed: {e}"); conn.rollback(); return False
    finally:
        cur.close(); conn.close()


# ── Data fetching ──────────────────────────────────────────────────────────────
fetch_pipeline_summary      = lambda: db_query("SELECT status, COUNT(*) AS count FROM leads GROUP BY status ORDER BY count DESC")
fetch_leads_today           = lambda: db_query("SELECT full_name, company_name, email, emirate, services_required, urgency, status, source, created_at FROM leads WHERE created_at >= NOW() - INTERVAL '24 hours' ORDER BY created_at DESC")
fetch_all_leads             = lambda: db_query("SELECT id, full_name, company_name, email, phone, emirate, services_required, urgency, status, source, email_status, touch1_sent, touch2_sent, touch3_sent, touch1_sent_at, touch2_sent_at, created_at FROM leads ORDER BY created_at DESC")
fetch_agent_stats           = lambda: (db_query("SELECT COUNT(*) AS total_conversations, COUNT(*) FILTER (WHERE lead_captured=TRUE) AS leads_captured, COUNT(*) FILTER (WHERE started_at >= NOW() - INTERVAL '24 hours') AS today, COUNT(*) FILTER (WHERE started_at >= NOW() - INTERVAL '7 days') AS this_week FROM agent_conversations") or [{}])[0]
fetch_recent_agent_convs    = lambda: db_query("SELECT visitor_name, visitor_company, visitor_email, visitor_phone, services_enquired, lead_captured, started_at FROM agent_conversations ORDER BY started_at DESC LIMIT 10")
fetch_outreach_stats        = lambda: (db_query("SELECT COUNT(*) FILTER (WHERE touch_number=1) AS touch1, COUNT(*) FILTER (WHERE touch_number=2) AS touch2, COUNT(*) FILTER (WHERE touch_number=3) AS touch3, COUNT(*) AS total_sent, COUNT(*) FILTER (WHERE response_received=TRUE) AS responses FROM outreach_log") or [{}])[0]
fetch_recent_outreach       = lambda: db_query("SELECT ol.company_name, l.full_name, ol.touch_number, ol.channel, ol.template_name, ol.sent_at, ol.response_received FROM outreach_log ol LEFT JOIN leads l ON ol.lead_id = l.id ORDER BY ol.sent_at DESC LIMIT 15")
fetch_upcoming_posts        = lambda: db_query("SELECT platform, post_type, caption, scheduled_at, status FROM social_posts WHERE scheduled_at >= NOW() ORDER BY scheduled_at ASC LIMIT 8")
fetch_form_submissions      = lambda: (db_query("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE lead_id IS NOT NULL) AS processed, COUNT(*) FILTER (WHERE lead_id IS NULL) AS unprocessed FROM form_submissions") or [{}])[0]
fetch_source_breakdown      = lambda: db_query("SELECT source, COUNT(*) AS count FROM leads GROUP BY source ORDER BY count DESC")
fetch_event_summary         = lambda: db_query("SELECT event_type, COUNT(*) AS count FROM lead_events GROUP BY event_type ORDER BY count DESC")

def fetch_overdue_touches():
    return db_query("""
        SELECT id, full_name, company_name, email, phone, touch1_sent_at, touch2_sent_at,
               CASE
                 WHEN touch2_sent=FALSE AND touch1_sent=TRUE AND touch1_sent_at <= NOW()-INTERVAL '3 days' THEN 'Touch 2 Overdue'
                 WHEN touch3_sent=FALSE AND touch2_sent=TRUE AND touch2_sent_at <= NOW()-INTERVAL '3 days' THEN 'Touch 3 Overdue'
               END AS action_needed
        FROM leads
        WHERE status NOT IN ('won','lost')
          AND ((touch2_sent=FALSE AND touch1_sent=TRUE AND touch1_sent_at <= NOW()-INTERVAL '3 days')
            OR (touch3_sent=FALSE AND touch2_sent=TRUE AND touch2_sent_at <= NOW()-INTERVAL '3 days'))
        ORDER BY touch1_sent_at ASC
    """)


# ── Email ──────────────────────────────────────────────────────────────────────
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=15), reraise=True)
def _smtp_send(to, subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Robin Edwan — ECO Technology <{GMAIL_USER}>"
    msg["To"]      = to
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
        s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        s.sendmail(GMAIL_USER, [to], msg.as_string())


# ── Touch 2 / Touch 3 ─────────────────────────────────────────────────────────
def send_touch(lead, touch_num):
    name    = lead.get("full_name", "")
    company = lead.get("company_name", "")
    email   = lead.get("email", "")
    if not email:
        return False

    if touch_num == 2:
        subject = "Following up — ECO Technology Environmental Services"
        body = f"""<div style="font-family:sans-serif;max-width:600px;margin:0 auto;color:#1e293b">
<p>Dear {name},</p>
<p>I wanted to follow up on my previous message regarding environmental compliance services for <strong>{company}</strong>.</p>
<p>ECO Technology works with 80+ facilities across all 7 UAE Emirates — keeping them fully compliant with Ajman, Dubai, and Abu Dhabi municipality requirements.</p>
<p>Would you have 10 minutes this week for a quick call? I'd love to understand your facility's specific needs.</p>
<p>📞 <strong>+971 52 223 3989</strong> (call or WhatsApp)<br>
🌐 <a href="{WEBSITE_URL}">{WEBSITE_URL}</a></p>
<p>Best regards,<br><strong>Robin Edwan</strong><br>
General Manager — ECO Technology Environmental Protection Services LLC<br>
Ajman, UAE · ISO 14001 · ISO 9001 · Ajman Municipality Approved</p></div>"""
    else:
        subject = "Final follow-up — ECO Technology"
        body = f"""<div style="font-family:sans-serif;max-width:600px;margin:0 auto;color:#1e293b">
<p>Dear {name},</p>
<p>This is my final follow-up regarding environmental compliance for <strong>{company}</strong>.</p>
<p>If the timing isn't right, I completely understand — feel free to reach out whenever you're ready.</p>
<p>Quick reminder of our services: Grease trap cleaning · Sewage desludging · Biological treatment · AMC from AED 48,000/year · 24/7 emergency response.</p>
<p>📞 <strong>+971 52 223 3989</strong> · 🌐 <a href="{WEBSITE_URL}">{WEBSITE_URL}</a></p>
<p>Best regards,<br><strong>Robin Edwan</strong><br>
General Manager — ECO Technology Environmental Protection Services LLC</p></div>"""

    try:
        _smtp_send(email, subject, body)
        col = f"touch{touch_num}_sent"
        col_at = f"touch{touch_num}_sent_at"
        db_execute(f"UPDATE leads SET {col}=TRUE, {col_at}=NOW() WHERE id=%s", (lead["id"],))
        db_execute(
            "INSERT INTO outreach_log (lead_id, company_name, touch_number, channel, template_name, sent_at) VALUES (%s,%s,%s,'email',%s,NOW())",
            (lead["id"], company, touch_num, f"ECO Tech — Touch {touch_num} Auto")
        )
        log.info(f"Touch {touch_num} sent → {email} (lead #{lead['id']})")
        return True
    except RetryError as e:
        log.error(f"Touch {touch_num} FAILED → {email}: {e}")
        return False


def run_overdue_touches():
    overdue = fetch_overdue_touches()
    if not overdue:
        log.info("No overdue touches — all leads on schedule ✅")
        return
    for lead in overdue:
        action = lead.get("action_needed", "")
        if "Touch 2" in action:
            send_touch(lead, 2)
        elif "Touch 3" in action:
            send_touch(lead, 3)


# ── Formatting ─────────────────────────────────────────────────────────────────
def status_badge(s):
    c = {"new":"#3b82f6","contacted":"#8b5cf6","proposal_sent":"#f59e0b","won":"#10b981","lost":"#6b7280"}.get(s,"#e2e8f0")
    return f'<span style="background:{c};color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">{s.replace("_"," ").title()}</span>'

def urgency_badge(u):
    k = (u or "").lower().split()[0] if u else ""
    c = {"urgent":"#ef4444","planned":"#3b82f6","exploring":"#6b7280"}.get(k,"#e2e8f0")
    return f'<span style="background:{c};color:#fff;padding:2px 6px;border-radius:8px;font-size:10px;">{u or "—"}</span>'

def fmt_date(s):
    if not s or str(s) in ("None",""):
        return "—"
    try:
        return datetime.fromisoformat(str(s).replace("Z","+00:00")).strftime("%d %b %Y, %H:%M")
    except Exception:
        return str(s)[:16]

tick = lambda v: "✅" if v else "—"


# ── HTML builder ───────────────────────────────────────────────────────────────
def build_report(pipeline, leads_today, all_leads, overdue, outreach_stats,
                 recent_outreach, agent_stats, agent_convs, posts, form_stats,
                 source_breakdown, event_summary):

    total      = len(all_leads)
    new_today  = len(leads_today)
    t_sent     = int(outreach_stats.get("total_sent") or 0)
    responses  = int(outreach_stats.get("responses") or 0)
    rr         = f"{responses/t_sent*100:.1f}%" if t_sent else "0%"
    ag_total   = int(agent_stats.get("total_conversations") or 0)
    ag_leads   = int(agent_stats.get("leads_captured") or 0)
    ag_today   = int(agent_stats.get("today") or 0)
    ag_rate    = f"{ag_leads/ag_total*100:.1f}%" if ag_total else "0%"

    alert = f'<div class="alert">⚡ <strong>{len(overdue)} lead(s) overdue</strong> — Touch 2/3 auto-fired. Check leads below.</div>' if overdue else ""

    def rows(data, fn): return "".join(fn(r) for r in data) if data else ""

    pipeline_rows = rows(pipeline, lambda r: f'<tr><td style="padding:8px 12px;">{status_badge(r["status"])}</td><td style="padding:8px 12px;text-align:center;font-weight:700;font-size:18px;">{r["count"]}</td></tr>')

    action_rows = rows(overdue, lambda r: f'''<tr style="background:#fffbeb;border-bottom:1px solid #fef3c7;">
      <td style="padding:8px 10px;font-weight:600;">{r.get("full_name","—")}</td>
      <td style="padding:8px 10px;">{r.get("company_name","—")}</td>
      <td style="padding:8px 10px;">{r.get("email","—")}</td>
      <td style="padding:8px 10px;">{r.get("phone","—")}</td>
      <td style="padding:8px 10px;text-align:center;"><span style="background:#f59e0b;color:#fff;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:700;">{r.get("action_needed","—")}</span></td>
      <td style="padding:8px 10px;">{fmt_date(str(r.get("touch1_sent_at","")))} </td></tr>''') or \
      '<tr><td colspan="6" style="padding:16px;text-align:center;color:#10b981;font-weight:600;">✅ All leads on schedule</td></tr>'

    lead_rows = rows(all_leads, lambda r: f'''<tr style="border-bottom:1px solid #f1f5f9;">
      <td style="padding:8px 10px;font-weight:600;">{r.get("full_name","—")}</td>
      <td style="padding:8px 10px;">{r.get("company_name","—")}</td>
      <td style="padding:8px 10px;">{r.get("emirate","—")}</td>
      <td style="padding:8px 10px;font-size:11px;color:#64748b;">{", ".join(r.get("services_required") or []) or "—"}</td>
      <td style="padding:8px 10px;">{urgency_badge(r.get("urgency",""))}</td>
      <td style="padding:8px 10px;">{status_badge(r.get("status","new"))}</td>
      <td style="padding:8px 10px;font-size:11px;color:#64748b;">{r.get("email_status","—")}</td>
      <td style="padding:8px 10px;text-align:center;">{tick(r.get("touch1_sent"))}</td>
      <td style="padding:8px 10px;text-align:center;">{tick(r.get("touch2_sent"))}</td>
      <td style="padding:8px 10px;text-align:center;">{tick(r.get("touch3_sent"))}</td>
      <td style="padding:8px 10px;font-size:11px;color:#94a3b8;">{fmt_date(str(r.get("created_at","")))} </td></tr>''')

    outreach_rows = rows(recent_outreach, lambda r: f'''<tr style="border-bottom:1px solid #f1f5f9;">
      <td style="padding:8px 10px;font-weight:600;">{r.get("company_name","—")}</td>
      <td style="padding:8px 10px;">{r.get("full_name","—")}</td>
      <td style="padding:8px 10px;text-align:center;">Touch {r.get("touch_number","?")}</td>
      <td style="padding:8px 10px;">{str(r.get("channel","—")).title()}</td>
      <td style="padding:8px 10px;font-size:11px;color:#64748b;">{r.get("template_name","—")}</td>
      <td style="padding:8px 10px;font-size:11px;color:#94a3b8;">{fmt_date(str(r.get("sent_at","")))} </td>
      <td style="padding:8px 10px;text-align:center;">{"✅" if r.get("response_received") else "—"}</td></tr>''') or \
      '<tr><td colspan="7" style="padding:12px;color:#94a3b8;text-align:center;">No outreach yet</td></tr>'

    agent_rows = rows(agent_convs, lambda r: f'''<tr style="border-bottom:1px solid #f1f5f9;">
      <td style="padding:8px 10px;font-weight:600;">{r.get("visitor_name","Anonymous")}</td>
      <td style="padding:8px 10px;">{r.get("visitor_company","—")}</td>
      <td style="padding:8px 10px;">{r.get("visitor_email","—")}</td>
      <td style="padding:8px 10px;">{r.get("visitor_phone","—")}</td>
      <td style="padding:8px 10px;font-size:11px;">{", ".join(r.get("services_enquired") or []) or "—"}</td>
      <td style="padding:8px 10px;text-align:center;">{"✅ Lead" if r.get("lead_captured") else "💬 Chat"}</td>
      <td style="padding:8px 10px;font-size:11px;color:#94a3b8;">{fmt_date(str(r.get("started_at","")))} </td></tr>''') or \
      '<tr><td colspan="7" style="padding:16px;text-align:center;color:#94a3b8;">Robin is live — no chats yet</td></tr>'

    post_rows = rows(posts, lambda r: f'''<tr style="border-bottom:1px solid #f1f5f9;">
      <td style="padding:8px 10px;">{str(r.get("platform","")).title()}</td>
      <td style="padding:8px 10px;">{str(r.get("post_type","")).title()}</td>
      <td style="padding:8px 10px;font-size:11px;color:#475569;">{(r.get("caption") or "")[:80]}{"..." if len(r.get("caption") or "") > 80 else ""}</td>
      <td style="padding:8px 10px;font-size:11px;color:#94a3b8;">{fmt_date(str(r.get("scheduled_at","")))} </td>
      <td style="padding:8px 10px;text-align:center;"><span style="background:#10b981;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;">Scheduled</span></td></tr>''') or \
      '<tr><td colspan="5" style="padding:12px;color:#94a3b8;text-align:center;">No upcoming posts</td></tr>'

    source_rows = rows(source_breakdown, lambda r: f'''<tr style="border-bottom:1px solid #f1f5f9;">
      <td style="padding:8px 12px;">{r.get("source","—")}</td>
      <td style="padding:8px 12px;text-align:center;font-weight:700;">{r.get("count",0)}</td>
      <td style="padding:8px 12px;text-align:center;color:#64748b;">{f"{int(r.get('count',0))/total*100:.0f}%" if total else "0%"}</td></tr>''')

    event_rows = rows(event_summary, lambda r: f'''<tr style="border-bottom:1px solid #f1f5f9;">
      <td style="padding:8px 12px;font-family:monospace;font-size:11px;">{r.get("event_type","—")}</td>
      <td style="padding:8px 12px;text-align:center;font-weight:700;">{r.get("count",0)}</td></tr>''') or \
      '<tr><td colspan="2" style="padding:12px;color:#94a3b8;text-align:center;">No events yet</td></tr>'

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;margin:0;padding:0;color:#1e293b}}
.container{{max-width:960px;margin:0 auto;padding:24px}}
.header{{background:linear-gradient(135deg,#065f46,#059669);color:#fff;padding:28px 32px;border-radius:12px;margin-bottom:24px}}
.header h1{{margin:0 0 4px;font-size:22px;font-weight:700}}.header p{{margin:0;opacity:.85;font-size:13px}}
.kpi-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:24px}}
.kpi{{background:#fff;border-radius:10px;padding:16px 18px;box-shadow:0 1px 4px rgba(0,0,0,.06);border-left:4px solid #059669}}
.kpi .value{{font-size:28px;font-weight:800;color:#059669;margin:0}}.kpi .label{{font-size:11px;color:#64748b;margin:4px 0 0;font-weight:500;text-transform:uppercase;letter-spacing:.5px}}
.section{{background:#fff;border-radius:10px;padding:20px 24px;margin-bottom:20px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.section h2{{margin:0 0 16px;font-size:15px;font-weight:700;color:#0f172a;border-bottom:2px solid #f1f5f9;padding-bottom:10px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th{{background:#f8fafc;padding:8px 10px;text-align:left;font-weight:600;color:#475569;font-size:11px;text-transform:uppercase;letter-spacing:.5px}}
.alert{{background:#fef3c7;border:1px solid #fcd34d;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:#92400e}}
.footer{{text-align:center;color:#94a3b8;font-size:11px;margin-top:24px;padding-top:16px;border-top:1px solid #e2e8f0}}
a{{color:#059669}}
</style></head><body><div class="container">
<div class="header"><h1>🌿 ECO Technology EPS — Daily Intelligence Report v3.0</h1>
<p>{REPORT_DATE} &nbsp;|&nbsp; {REPORT_TIMESTAMP} &nbsp;|&nbsp; <a href="{AGENT_APP_URL}" style="color:#a7f3d0;">Robin AI Agent</a> &nbsp;|&nbsp; <a href="{WEBSITE_URL}" style="color:#a7f3d0;">Website</a></p></div>
{alert}
<div class="kpi-grid">
  <div class="kpi"><p class="value">{total}</p><p class="label">Total Leads</p></div>
  <div class="kpi" style="border-left-color:#3b82f6;"><p class="value" style="color:#3b82f6;">{new_today}</p><p class="label">New Today</p></div>
  <div class="kpi" style="border-left-color:#8b5cf6;"><p class="value" style="color:#8b5cf6;">{ag_total}</p><p class="label">Robin Chats</p></div>
  <div class="kpi" style="border-left-color:#f59e0b;"><p class="value" style="color:#f59e0b;">{rr}</p><p class="label">Response Rate</p></div>
  <div class="kpi" style="border-left-color:#ef4444;"><p class="value" style="color:#ef4444;">{len(overdue)}</p><p class="label">Overdue</p></div>
</div>
<div class="section" style="border-left:4px solid #f59e0b;"><h2>⚡ Action Required</h2>
<table><tr><th>Name</th><th>Company</th><th>Email</th><th>Phone</th><th>Action</th><th>T1 Sent</th></tr>{action_rows}</table></div>
<div class="section"><h2>📊 Pipeline Status</h2>
<table><tr><th>Stage</th><th>Count</th></tr>{pipeline_rows}</table></div>
<div class="section" style="border-left:4px solid #8b5cf6;"><h2>🤖 Robin AI Agent</h2>
<table style="margin-bottom:16px;"><tr><th>Total Chats</th><th>Today</th><th>This Week</th><th>Leads Captured</th><th>Conversion</th></tr>
<tr><td style="padding:10px;font-size:16px;font-weight:700;">{ag_total}</td><td style="padding:10px;font-size:16px;font-weight:700;color:#8b5cf6;">{ag_today}</td>
<td style="padding:10px;font-size:16px;font-weight:700;">{int(agent_stats.get("this_week") or 0)}</td>
<td style="padding:10px;font-size:16px;font-weight:700;color:#10b981;">{ag_leads}</td>
<td style="padding:10px;font-size:16px;font-weight:700;color:#f59e0b;">{ag_rate}</td></tr></table>
<table><tr><th>Visitor</th><th>Company</th><th>Email</th><th>Phone</th><th>Services</th><th>Outcome</th><th>Time</th></tr>{agent_rows}</table></div>
<div class="section"><h2>👥 All Leads ({total})</h2>
<table><tr><th>Name</th><th>Company</th><th>Emirate</th><th>Services</th><th>Urgency</th><th>Status</th><th>Email</th><th>T1</th><th>T2</th><th>T3</th><th>Created</th></tr>{lead_rows}</table></div>
<div class="section"><h2>📡 Lead Sources</h2>
<table><tr><th>Source</th><th>Count</th><th>Share</th></tr>{source_rows}</table></div>
<div class="section"><h2>📬 Outreach</h2>
<table style="margin-bottom:16px;"><tr><th>Total Sent</th><th>Touch 1</th><th>Touch 2</th><th>Touch 3</th><th>Responses</th><th>Rate</th></tr>
<tr><td style="padding:10px;font-size:16px;font-weight:700;">{t_sent}</td>
<td style="padding:10px;font-size:16px;font-weight:700;">{int(outreach_stats.get("touch1") or 0)}</td>
<td style="padding:10px;font-size:16px;font-weight:700;">{int(outreach_stats.get("touch2") or 0)}</td>
<td style="padding:10px;font-size:16px;font-weight:700;">{int(outreach_stats.get("touch3") or 0)}</td>
<td style="padding:10px;font-size:16px;font-weight:700;color:#10b981;">{responses}</td>
<td style="padding:10px;font-size:16px;font-weight:700;color:#f59e0b;">{rr}</td></tr></table>
<table><tr><th>Company</th><th>Contact</th><th>Touch</th><th>Channel</th><th>Template</th><th>Sent At</th><th>Response</th></tr>{outreach_rows}</table></div>
<div class="section"><h2>📸 Upcoming Posts</h2>
<table><tr><th>Platform</th><th>Type</th><th>Caption</th><th>Scheduled</th><th>Status</th></tr>{post_rows}</table></div>
<div class="section"><h2>📝 Jotform Submissions</h2>
<table><tr><th>Total</th><th>Processed</th><th>Unprocessed</th></tr>
<tr><td style="padding:10px;font-size:16px;font-weight:700;">{form_stats.get("total") or 0}</td>
<td style="padding:10px;font-size:16px;font-weight:700;color:#10b981;">{form_stats.get("processed") or 0}</td>
<td style="padding:10px;font-size:16px;font-weight:700;color:#ef4444;">{form_stats.get("unprocessed") or 0}</td></tr></table></div>
<div class="section"><h2>⚙️ Event Log</h2>
<table><tr><th>Event Type</th><th>Count</th></tr>{event_rows}</table></div>
<div class="footer">ECO Technology EPS — Daily Intelligence Report v3.0 &nbsp;|&nbsp; {REPORT_TIMESTAMP}<br>
Self-hosted · Direct DB · No external dependencies<br>
<a href="{AGENT_APP_URL}">Robin AI Agent</a> &nbsp;|&nbsp; <a href="{WEBSITE_URL}">Website</a> &nbsp;|&nbsp; <a href="{QUOTE_FORM_URL}">Quote Form</a></div>
</div></body></html>"""


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    log.info(f"ECO Daily Report v3.0 — {REPORT_TIMESTAMP}")

    log.info("Firing overdue Touch 2 / Touch 3 emails...")
    run_overdue_touches()

    log.info("Fetching pipeline data...")
    html = build_report(
        pipeline       = fetch_pipeline_summary(),
        leads_today    = fetch_leads_today(),
        all_leads      = fetch_all_leads(),
        overdue        = fetch_overdue_touches(),
        outreach_stats = fetch_outreach_stats(),
        recent_outreach= fetch_recent_outreach(),
        agent_stats    = fetch_agent_stats(),
        agent_convs    = fetch_recent_agent_convs(),
        posts          = fetch_upcoming_posts(),
        form_stats     = fetch_form_submissions(),
        source_breakdown = fetch_source_breakdown(),
        event_summary  = fetch_event_summary(),
    )

    overdue_count = len(fetch_overdue_touches())
    subject = f"⚡ ACTION NEEDED — {overdue_count} overdue | {REPORT_DATE}" if overdue_count else f"🌿 ECO Technology — Daily Report | {REPORT_DATE}"

    log.info(f"Sending report to {RECIPIENT_EMAIL}...")
    try:
        _smtp_send(RECIPIENT_EMAIL, subject, html)
        log.info("✅ Report delivered")
        return 0
    except RetryError as e:
        log.error(f"❌ Report delivery failed: {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
