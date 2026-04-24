#!/usr/bin/env python3.11
"""
ECO Technology EPS — Daily Lead Report v2.0
Queries Neon DB for lead activity, pipeline status, outreach stats,
Robin AI agent conversation stats, and upcoming posts.
Emails a full HTML summary to robenedwan@gmail.com.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone

# ── Config ─────────────────────────────────────────────────────────────────────
NEON_PROJECT_ID = "square-hill-70076734"
RECIPIENT_EMAIL = "robenedwan@gmail.com"
REPORT_DATE = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
REPORT_TIMESTAMP = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
AGENT_ID = "019dbd271be77876b7e36ce06ac88fd491ef"
AGENT_APP_URL = "https://www.jotform.com/app/261127959395470"
WEBSITE_URL = "https://binz2008-star.github.io/eco-environmental"
QUOTE_FORM_URL = "https://form.jotform.com/261008161739051"


# ── Neon Query Helper ──────────────────────────────────────────────────────────
def neon_query(sql: str) -> list:
    """Run a SQL query against Neon DB via MCP CLI and return parsed JSON rows."""
    payload = json.dumps({"projectId": NEON_PROJECT_ID, "sql": sql})
    result = subprocess.run(
        ["manus-mcp-cli", "tool", "call", "run_sql", "--server", "neon", "--input", payload],
        capture_output=True, text=True, timeout=30
    )
    output = result.stdout
    start = output.find("[")
    end = output.rfind("]") + 1
    if start == -1 or end == 0:
        return []
    try:
        return json.loads(output[start:end])
    except json.JSONDecodeError:
        return []


# ── Data Fetching ──────────────────────────────────────────────────────────────
def fetch_pipeline_summary() -> list:
    return neon_query("""
        SELECT status, COUNT(*) AS count
        FROM leads
        GROUP BY status
        ORDER BY count DESC
    """)


def fetch_leads_today() -> list:
    return neon_query("""
        SELECT full_name, company_name, job_title, email, emirate,
               services_required, urgency, status, source, created_at
        FROM leads
        WHERE created_at >= NOW() - INTERVAL '24 hours'
        ORDER BY created_at DESC
    """)


def fetch_all_leads() -> list:
    return neon_query("""
        SELECT id, full_name, company_name, job_title, email, emirate,
               services_required, urgency, status, source,
               touch1_sent, touch2_sent, touch3_sent, created_at
        FROM leads
        ORDER BY created_at DESC
    """)


def fetch_overdue_touches() -> list:
    """Leads that need Touch 2 (T1 sent 3+ days ago, no T2) or Touch 3 (T2 sent 3+ days ago, no T3)."""
    return neon_query("""
        SELECT id, full_name, company_name, email, status,
               touch1_sent, touch1_sent_at,
               touch2_sent, touch2_sent_at,
               touch3_sent,
               CASE
                 WHEN touch2_sent = FALSE AND touch1_sent = TRUE
                      AND touch1_sent_at <= NOW() - INTERVAL '3 days' THEN 'Touch 2 Overdue'
                 WHEN touch3_sent = FALSE AND touch2_sent = TRUE
                      AND touch2_sent_at <= NOW() - INTERVAL '3 days' THEN 'Touch 3 Overdue'
               END AS action_needed
        FROM leads
        WHERE status NOT IN ('won', 'lost')
          AND (
            (touch2_sent = FALSE AND touch1_sent = TRUE AND touch1_sent_at <= NOW() - INTERVAL '3 days')
            OR
            (touch3_sent = FALSE AND touch2_sent = TRUE AND touch2_sent_at <= NOW() - INTERVAL '3 days')
          )
        ORDER BY touch1_sent_at ASC
    """)


def fetch_outreach_stats() -> dict:
    rows = neon_query("""
        SELECT
            COUNT(*) AS total_sent,
            SUM(CASE WHEN response_received THEN 1 ELSE 0 END) AS responses,
            SUM(CASE WHEN touch_number = 1 THEN 1 ELSE 0 END) AS touch1,
            SUM(CASE WHEN touch_number = 2 THEN 1 ELSE 0 END) AS touch2,
            SUM(CASE WHEN touch_number = 3 THEN 1 ELSE 0 END) AS touch3
        FROM outreach_log
    """)
    return rows[0] if rows else {}


def fetch_recent_outreach() -> list:
    return neon_query("""
        SELECT ol.touch_number, ol.channel, ol.template_name,
               ol.sent_at, ol.status, ol.response_received,
               l.full_name, l.company_name
        FROM outreach_log ol
        LEFT JOIN leads l ON ol.lead_id = l.id
        ORDER BY ol.sent_at DESC
        LIMIT 15
    """)


def fetch_agent_stats() -> dict:
    """Fetch Robin AI agent conversation statistics."""
    rows = neon_query("""
        SELECT
            COUNT(*) AS total_conversations,
            SUM(CASE WHEN lead_captured = TRUE THEN 1 ELSE 0 END) AS leads_captured,
            SUM(CASE WHEN lead_captured = FALSE THEN 1 ELSE 0 END) AS no_lead,
            COUNT(CASE WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 1 END) AS today,
            COUNT(CASE WHEN created_at >= NOW() - INTERVAL '7 days' THEN 1 END) AS this_week
        FROM agent_conversations
    """)
    return rows[0] if rows else {
        "total_conversations": 0, "leads_captured": 0,
        "no_lead": 0, "today": 0, "this_week": 0
    }


def fetch_recent_agent_conversations() -> list:
    """Fetch the 10 most recent Robin agent conversations."""
    return neon_query("""
        SELECT visitor_name, visitor_company, visitor_email, visitor_phone,
               services_enquired, emirate, lead_captured, conversation_summary,
               created_at
        FROM agent_conversations
        ORDER BY created_at DESC
        LIMIT 10
    """)


def fetch_upcoming_posts() -> list:
    return neon_query("""
        SELECT id, platform, post_type, caption, status, scheduled_at
        FROM social_posts
        WHERE status = 'scheduled' AND scheduled_at IS NOT NULL
        ORDER BY scheduled_at ASC
        LIMIT 10
    """)


def fetch_form_submissions() -> dict:
    rows = neon_query("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN processed THEN 1 ELSE 0 END) AS processed,
            SUM(CASE WHEN NOT processed THEN 1 ELSE 0 END) AS unprocessed
        FROM form_submissions
    """)
    return rows[0] if rows else {"total": 0, "processed": 0, "unprocessed": 0}


def fetch_source_breakdown() -> list:
    """Lead source breakdown for funnel analysis."""
    return neon_query("""
        SELECT COALESCE(source, 'Unknown') AS source, COUNT(*) AS count
        FROM leads
        GROUP BY source
        ORDER BY count DESC
    """)


# ── HTML Helpers ───────────────────────────────────────────────────────────────
def status_badge(status: str) -> str:
    colors = {
        "new": "#3b82f6", "proposal_sent": "#f59e0b",
        "qualified": "#8b5cf6", "won": "#10b981",
        "lost": "#ef4444", "nurturing": "#6366f1",
    }
    color = colors.get((status or "new").lower(), "#6b7280")
    label = (status or "New").replace("_", " ").title()
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;">{label}</span>'


def urgency_badge(urgency: str) -> str:
    colors = {"Urgent": "#ef4444", "Planned": "#3b82f6", "Exploring": "#6b7280"}
    color = colors.get(urgency or "", "#6b7280")
    return f'<span style="background:{color};color:#fff;padding:2px 6px;border-radius:8px;font-size:10px;">{urgency or "—"}</span>'


def fmt_date(iso_str: str) -> str:
    if not iso_str or iso_str == "None":
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M UTC")
    except Exception:
        return iso_str


# ── HTML Report Builder ────────────────────────────────────────────────────────
def build_html_report(
    pipeline: list,
    leads_today: list,
    all_leads: list,
    overdue_touches: list,
    outreach_stats: dict,
    recent_outreach: list,
    agent_stats: dict,
    recent_agent_convs: list,
    upcoming_posts: list,
    form_stats: dict,
    source_breakdown: list,
) -> str:
    total_leads = len(all_leads)
    new_today = len(leads_today)
    total_outreach = int(outreach_stats.get("total_sent") or 0)
    total_responses = int(outreach_stats.get("responses") or 0)
    response_rate = f"{(total_responses / total_outreach * 100):.1f}%" if total_outreach else "0%"
    touch1 = int(outreach_stats.get("touch1") or 0)
    touch2 = int(outreach_stats.get("touch2") or 0)
    touch3 = int(outreach_stats.get("touch3") or 0)
    agent_total = int(agent_stats.get("total_conversations") or 0)
    agent_leads = int(agent_stats.get("leads_captured") or 0)
    agent_today = int(agent_stats.get("today") or 0)
    agent_conv_rate = f"{(agent_leads / agent_total * 100):.1f}%" if agent_total else "0%"

    # ── Overdue action alerts ──
    alert_html = ""
    if overdue_touches:
        alert_html = f"""
  <div class="alert">
    ⚠️ <strong>{len(overdue_touches)} lead(s) require immediate follow-up action</strong> — overdue touches detected. See Action Required section below.
  </div>"""

    # ── Pipeline rows ──
    pipeline_rows = ""
    for row in pipeline:
        pipeline_rows += f"""
        <tr>
          <td style="padding:8px 12px;">{status_badge(row['status'])}</td>
          <td style="padding:8px 12px;text-align:center;font-weight:700;font-size:18px;">{row['count']}</td>
        </tr>"""

    # ── Action required rows ──
    action_rows = ""
    for lead in overdue_touches:
        action_rows += f"""
        <tr style="border-bottom:1px solid #fef3c7;background:#fffbeb;">
          <td style="padding:8px 10px;font-weight:600;">{lead.get('full_name','—')}</td>
          <td style="padding:8px 10px;">{lead.get('company_name','—')}</td>
          <td style="padding:8px 10px;">{lead.get('email','—')}</td>
          <td style="padding:8px 10px;text-align:center;">
            <span style="background:#f59e0b;color:#fff;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:700;">{lead.get('action_needed','—')}</span>
          </td>
          <td style="padding:8px 10px;">{fmt_date(str(lead.get('touch1_sent_at') or ''))}</td>
        </tr>"""
    if not action_rows:
        action_rows = '<tr><td colspan="5" style="padding:16px;text-align:center;color:#10b981;font-weight:600;">✅ All leads are on schedule — no overdue touches</td></tr>'

    # ── All leads rows ──
    lead_rows = ""
    for lead in all_leads:
        services = ", ".join(lead.get("services_required") or [])
        t1 = "✅" if lead.get("touch1_sent") else "—"
        t2 = "✅" if lead.get("touch2_sent") else "—"
        t3 = "✅" if lead.get("touch3_sent") else "—"
        src = lead.get("source") or "—"
        lead_rows += f"""
        <tr style="border-bottom:1px solid #f1f5f9;">
          <td style="padding:8px 10px;font-weight:600;">{lead['full_name']}</td>
          <td style="padding:8px 10px;">{lead['company_name']}</td>
          <td style="padding:8px 10px;">{lead.get('emirate') or '—'}</td>
          <td style="padding:8px 10px;font-size:11px;color:#64748b;">{services}</td>
          <td style="padding:8px 10px;">{urgency_badge(lead.get('urgency') or '—')}</td>
          <td style="padding:8px 10px;">{status_badge(lead.get('status') or 'new')}</td>
          <td style="padding:8px 10px;font-size:11px;color:#64748b;">{src}</td>
          <td style="padding:8px 10px;text-align:center;">{t1}</td>
          <td style="padding:8px 10px;text-align:center;">{t2}</td>
          <td style="padding:8px 10px;text-align:center;">{t3}</td>
          <td style="padding:8px 10px;font-size:11px;color:#94a3b8;">{fmt_date(str(lead.get('created_at') or ''))}</td>
        </tr>"""

    # ── Outreach rows ──
    outreach_rows = ""
    for row in recent_outreach:
        resp = "✅ Yes" if row.get("response_received") else "—"
        outreach_rows += f"""
        <tr style="border-bottom:1px solid #f1f5f9;">
          <td style="padding:8px 10px;font-weight:600;">{row.get('company_name') or '—'}</td>
          <td style="padding:8px 10px;">{row.get('full_name') or '—'}</td>
          <td style="padding:8px 10px;text-align:center;">Touch {row.get('touch_number', '?')}</td>
          <td style="padding:8px 10px;">{row.get('channel', '—').title()}</td>
          <td style="padding:8px 10px;font-size:11px;color:#64748b;">{row.get('template_name') or '—'}</td>
          <td style="padding:8px 10px;font-size:11px;color:#94a3b8;">{fmt_date(str(row.get('sent_at') or ''))}</td>
          <td style="padding:8px 10px;text-align:center;">{resp}</td>
        </tr>"""

    # ── Robin agent conversation rows ──
    agent_rows = ""
    for conv in recent_agent_convs:
        services = ", ".join(conv.get("services_enquired") or []) or "—"
        captured = "✅ Lead" if conv.get("lead_captured") else "💬 Chat"
        agent_rows += f"""
        <tr style="border-bottom:1px solid #f1f5f9;">
          <td style="padding:8px 10px;font-weight:600;">{conv.get('visitor_name') or 'Anonymous'}</td>
          <td style="padding:8px 10px;">{conv.get('visitor_company') or '—'}</td>
          <td style="padding:8px 10px;">{conv.get('visitor_email') or '—'}</td>
          <td style="padding:8px 10px;">{conv.get('visitor_phone') or '—'}</td>
          <td style="padding:8px 10px;font-size:11px;color:#64748b;">{services}</td>
          <td style="padding:8px 10px;">{conv.get('emirate') or '—'}</td>
          <td style="padding:8px 10px;text-align:center;">{captured}</td>
          <td style="padding:8px 10px;font-size:11px;color:#94a3b8;">{fmt_date(str(conv.get('created_at') or ''))}</td>
        </tr>"""
    if not agent_rows:
        agent_rows = '<tr><td colspan="8" style="padding:16px;text-align:center;color:#94a3b8;">No agent conversations recorded yet — Robin is live and ready</td></tr>'

    # ── Source breakdown rows ──
    source_rows = ""
    for row in source_breakdown:
        pct = f"{(int(row.get('count',0)) / total_leads * 100):.0f}%" if total_leads else "0%"
        source_rows += f"""
        <tr style="border-bottom:1px solid #f1f5f9;">
          <td style="padding:8px 12px;">{row.get('source','Unknown')}</td>
          <td style="padding:8px 12px;text-align:center;font-weight:700;">{row.get('count',0)}</td>
          <td style="padding:8px 12px;text-align:center;color:#64748b;">{pct}</td>
        </tr>"""

    # ── Post rows ──
    post_rows = ""
    for post in upcoming_posts:
        caption_preview = (post.get("caption") or "")[:80] + ("..." if len(post.get("caption") or "") > 80 else "")
        post_rows += f"""
        <tr style="border-bottom:1px solid #f1f5f9;">
          <td style="padding:8px 10px;">{post.get('platform', '').title()}</td>
          <td style="padding:8px 10px;">{post.get('post_type', '').title()}</td>
          <td style="padding:8px 10px;font-size:11px;color:#475569;">{caption_preview}</td>
          <td style="padding:8px 10px;font-size:11px;color:#94a3b8;">{fmt_date(str(post.get('scheduled_at') or ''))}</td>
          <td style="padding:8px 10px;text-align:center;"><span style="background:#10b981;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;">Scheduled</span></td>
        </tr>"""
    if not post_rows:
        post_rows = '<tr><td colspan="5" style="padding:16px;text-align:center;color:#94a3b8;">No upcoming scheduled posts</td></tr>'

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#f8fafc; margin:0; padding:0; color:#1e293b; }}
  .container {{ max-width:960px; margin:0 auto; padding:24px; }}
  .header {{ background:linear-gradient(135deg,#065f46,#059669); color:#fff; padding:28px 32px; border-radius:12px; margin-bottom:24px; }}
  .header h1 {{ margin:0 0 4px; font-size:22px; font-weight:700; }}
  .header p {{ margin:0; opacity:0.85; font-size:13px; }}
  .kpi-grid {{ display:grid; grid-template-columns:repeat(5,1fr); gap:14px; margin-bottom:24px; }}
  .kpi {{ background:#fff; border-radius:10px; padding:16px 18px; box-shadow:0 1px 4px rgba(0,0,0,0.06); border-left:4px solid #059669; }}
  .kpi .value {{ font-size:28px; font-weight:800; color:#059669; margin:0; }}
  .kpi .label {{ font-size:11px; color:#64748b; margin:4px 0 0; font-weight:500; text-transform:uppercase; letter-spacing:0.5px; }}
  .section {{ background:#fff; border-radius:10px; padding:20px 24px; margin-bottom:20px; box-shadow:0 1px 4px rgba(0,0,0,0.06); }}
  .section h2 {{ margin:0 0 16px; font-size:15px; font-weight:700; color:#0f172a; border-bottom:2px solid #f1f5f9; padding-bottom:10px; }}
  .section-agent {{ border-left:4px solid #8b5cf6; }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  th {{ background:#f8fafc; padding:8px 10px; text-align:left; font-weight:600; color:#475569; font-size:11px; text-transform:uppercase; letter-spacing:0.5px; }}
  .footer {{ text-align:center; color:#94a3b8; font-size:11px; margin-top:24px; padding-top:16px; border-top:1px solid #e2e8f0; }}
  .alert {{ background:#fef3c7; border:1px solid #fcd34d; border-radius:8px; padding:12px 16px; margin-bottom:16px; font-size:13px; color:#92400e; }}
  .agent-badge {{ background:#8b5cf6;color:#fff;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600; }}
  a {{ color:#059669; }}
</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <div class="header">
    <h1>🌿 ECO Technology EPS — Daily Intelligence Report</h1>
    <p>{REPORT_DATE} &nbsp;|&nbsp; Generated at {REPORT_TIMESTAMP} &nbsp;|&nbsp; Robin AI Agent: <a href="{AGENT_APP_URL}" style="color:#a7f3d0;">{AGENT_APP_URL}</a></p>
  </div>

  {alert_html}

  <!-- KPI Cards -->
  <div class="kpi-grid">
    <div class="kpi">
      <p class="value">{total_leads}</p>
      <p class="label">Total Leads</p>
    </div>
    <div class="kpi" style="border-left-color:#3b82f6;">
      <p class="value" style="color:#3b82f6;">{new_today}</p>
      <p class="label">New Today</p>
    </div>
    <div class="kpi" style="border-left-color:#8b5cf6;">
      <p class="value" style="color:#8b5cf6;">{agent_total}</p>
      <p class="label">Robin Conversations</p>
    </div>
    <div class="kpi" style="border-left-color:#f59e0b;">
      <p class="value" style="color:#f59e0b;">{response_rate}</p>
      <p class="label">Email Response Rate</p>
    </div>
    <div class="kpi" style="border-left-color:#ef4444;">
      <p class="value" style="color:#ef4444;">{len(overdue_touches)}</p>
      <p class="label">Overdue Actions</p>
    </div>
  </div>

  <!-- Action Required -->
  <div class="section" style="border-left:4px solid #f59e0b;">
    <h2>⚡ Action Required — Overdue Follow-Ups</h2>
    <table>
      <tr><th>Name</th><th>Company</th><th>Email</th><th>Action</th><th>Touch 1 Sent</th></tr>
      {action_rows}
    </table>
  </div>

  <!-- Pipeline Status -->
  <div class="section">
    <h2>📊 Pipeline Status</h2>
    <table>
      <tr><th>Stage</th><th>Count</th></tr>
      {pipeline_rows}
    </table>
  </div>

  <!-- Robin AI Agent Stats -->
  <div class="section section-agent">
    <h2>🤖 Robin AI Agent — Conversation Intelligence</h2>
    <table style="margin-bottom:16px;">
      <tr>
        <th>Total Conversations</th><th>Today</th><th>This Week</th>
        <th>Leads Captured</th><th>Conversion Rate</th>
      </tr>
      <tr>
        <td style="padding:10px;font-size:16px;font-weight:700;">{agent_total}</td>
        <td style="padding:10px;font-size:16px;font-weight:700;color:#8b5cf6;">{agent_today}</td>
        <td style="padding:10px;font-size:16px;font-weight:700;">{int(agent_stats.get('this_week') or 0)}</td>
        <td style="padding:10px;font-size:16px;font-weight:700;color:#10b981;">{agent_leads}</td>
        <td style="padding:10px;font-size:16px;font-weight:700;color:#f59e0b;">{agent_conv_rate}</td>
      </tr>
    </table>
    <h3 style="font-size:13px;color:#475569;margin:16px 0 10px;">Recent Conversations</h3>
    <table>
      <tr>
        <th>Visitor</th><th>Company</th><th>Email</th><th>Phone</th>
        <th>Services</th><th>Emirate</th><th>Outcome</th><th>Time</th>
      </tr>
      {agent_rows}
    </table>
  </div>

  <!-- All Leads -->
  <div class="section">
    <h2>👥 All Leads ({total_leads} total)</h2>
    <table>
      <tr>
        <th>Name</th><th>Company</th><th>Emirate</th><th>Services</th>
        <th>Urgency</th><th>Status</th><th>Source</th><th>T1</th><th>T2</th><th>T3</th><th>Created</th>
      </tr>
      {lead_rows}
    </table>
  </div>

  <!-- Lead Source Breakdown -->
  <div class="section">
    <h2>📡 Lead Source Breakdown</h2>
    <table>
      <tr><th>Source</th><th>Count</th><th>Share</th></tr>
      {source_rows}
    </table>
  </div>

  <!-- Outreach Stats -->
  <div class="section">
    <h2>📬 Outreach Summary</h2>
    <table>
      <tr>
        <th>Total Sent</th><th>Touch 1</th><th>Touch 2</th><th>Touch 3</th>
        <th>Responses</th><th>Response Rate</th>
      </tr>
      <tr>
        <td style="padding:10px;font-size:16px;font-weight:700;">{total_outreach}</td>
        <td style="padding:10px;font-size:16px;font-weight:700;">{touch1}</td>
        <td style="padding:10px;font-size:16px;font-weight:700;">{touch2}</td>
        <td style="padding:10px;font-size:16px;font-weight:700;">{touch3}</td>
        <td style="padding:10px;font-size:16px;font-weight:700;color:#10b981;">{total_responses}</td>
        <td style="padding:10px;font-size:16px;font-weight:700;color:#f59e0b;">{response_rate}</td>
      </tr>
    </table>
  </div>

  <!-- Recent Outreach Log -->
  <div class="section">
    <h2>📋 Recent Outreach Activity (Last 15)</h2>
    <table>
      <tr>
        <th>Company</th><th>Contact</th><th>Touch</th><th>Channel</th>
        <th>Template</th><th>Sent At</th><th>Response</th>
      </tr>
      {outreach_rows}
    </table>
  </div>

  <!-- Upcoming Posts -->
  <div class="section">
    <h2>📸 Upcoming Instagram Posts</h2>
    <table>
      <tr><th>Platform</th><th>Type</th><th>Caption Preview</th><th>Scheduled At</th><th>Status</th></tr>
      {post_rows}
    </table>
  </div>

  <!-- Form Submissions -->
  <div class="section">
    <h2>📝 Jotform Form Submissions</h2>
    <table>
      <tr><th>Total Received</th><th>Processed</th><th>Unprocessed</th></tr>
      <tr>
        <td style="padding:10px;font-size:16px;font-weight:700;">{form_stats.get('total') or 0}</td>
        <td style="padding:10px;font-size:16px;font-weight:700;color:#10b981;">{form_stats.get('processed') or 0}</td>
        <td style="padding:10px;font-size:16px;font-weight:700;color:#ef4444;">{form_stats.get('unprocessed') or 0}</td>
      </tr>
    </table>
  </div>

  <div class="footer">
    ECO Technology EPS — Daily Intelligence Report v2.0 &nbsp;|&nbsp; {REPORT_TIMESTAMP}<br>
    Powered by Manus AI &nbsp;|&nbsp; Robin AI Agent: <a href="{AGENT_APP_URL}">{AGENT_APP_URL}</a> &nbsp;|&nbsp;
    Website: <a href="{WEBSITE_URL}">{WEBSITE_URL}</a>
  </div>

</div>
</body>
</html>"""
    return html


# ── Email Sender via Gmail MCP ─────────────────────────────────────────────────
def send_email(subject: str, html_body: str) -> bool:
    payload = json.dumps({
        "messages": [{
            "to": RECIPIENT_EMAIL,
            "subject": subject,
            "body": html_body,
            "mimeType": "text/html"
        }]
    })
    result = subprocess.run(
        ["manus-mcp-cli", "tool", "call", "gmail_send_messages", "--server", "gmail", "--input", payload],
        capture_output=True, text=True, timeout=60
    )
    output = result.stdout + result.stderr
    print(output)
    return "error" not in output.lower() or "sent" in output.lower()


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"[{REPORT_TIMESTAMP}] ECO Technology Daily Report v2.0 — Starting...")

    print("  → Fetching pipeline summary...")
    pipeline = fetch_pipeline_summary()

    print("  → Fetching new leads (last 24h)...")
    leads_today = fetch_leads_today()

    print("  → Fetching all leads...")
    all_leads = fetch_all_leads()

    print("  → Checking overdue follow-up actions...")
    overdue_touches = fetch_overdue_touches()

    print("  → Fetching outreach stats...")
    outreach_stats = fetch_outreach_stats()

    print("  → Fetching recent outreach log...")
    recent_outreach = fetch_recent_outreach()

    print("  → Fetching Robin AI agent stats...")
    agent_stats = fetch_agent_stats()

    print("  → Fetching recent agent conversations...")
    recent_agent_convs = fetch_recent_agent_conversations()

    print("  → Fetching upcoming Instagram posts...")
    upcoming_posts = fetch_upcoming_posts()

    print("  → Fetching form submissions...")
    form_stats = fetch_form_submissions()

    print("  → Fetching lead source breakdown...")
    source_breakdown = fetch_source_breakdown()

    print("  → Building HTML report...")
    html = build_html_report(
        pipeline, leads_today, all_leads, overdue_touches,
        outreach_stats, recent_outreach,
        agent_stats, recent_agent_convs,
        upcoming_posts, form_stats, source_breakdown
    )

    subject = f"ECO Technology — Daily Intelligence Report | {REPORT_DATE}"
    if overdue_touches:
        subject = f"⚡ ACTION NEEDED — {subject}"

    print(f"  → Sending email to {RECIPIENT_EMAIL}...")
    success = send_email(subject, html)

    if success:
        print(f"  ✅ Report sent successfully to {RECIPIENT_EMAIL}")
    else:
        print(f"  ⚠️  Email send may have issues — check output above")

    report_path = f"/home/ubuntu/automation/report_{datetime.now(timezone.utc).strftime('%Y%m%d')}.html"
    with open(report_path, "w") as f:
        f.write(html)
    print(f"  📄 Local copy saved: {report_path}")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
