#!/usr/bin/env python3
"""
ECO Technology — Lead Score Backfill
======================================
One-time script to score all existing leads that have no score yet.
Safe to run multiple times — skips already-scored leads.

Usage:
  python pipeline/backfill_lead_scores.py

Requires: DATABASE_URL env var (or uses hardcoded Neon URL fallback)
"""

import os
import json
import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from lead_scorer import score_lead

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_y5BACw4WZOqf@ep-super-cake-amzu8ims-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require"
)


def get_db():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def fetch_unscored_leads() -> list:
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, full_name, company_name, email, phone,
                   services_required, urgency, emirate, source, status
            FROM leads
            WHERE lead_score IS NULL
            ORDER BY created_at ASC
        """)
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


def update_lead_score(lead_id: int, scoring: dict):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE leads SET
                lead_score         = %s,
                score_band         = %s,
                rag_intent         = %s,
                rag_confidence     = %s,
                rag_method         = %s,
                recommended_action = %s,
                scored_at          = NOW()
            WHERE id = %s
        """, (
            scoring.get("score"),
            scoring.get("band"),
            "eco",           # heuristic-only during backfill
            0.0,
            "backfill_heuristic",
            scoring.get("recommended_action"),
            lead_id,
        ))
        conn.commit()
    except Exception as e:
        log.error(f"Failed to update lead #{lead_id}: {e}")
        conn.rollback()
    finally:
        cur.close(); conn.close()


def main():
    log.info("ECO Lead Score Backfill — starting")
    leads = fetch_unscored_leads()
    log.info(f"Found {len(leads)} unscored leads")

    if not leads:
        log.info("All leads already scored — nothing to do")
        return

    hot = warm = medium = cold = 0

    for lead in leads:
        # Normalize lead dict for scorer
        normalized = {
            "name":    lead.get("full_name", ""),
            "company": lead.get("company_name", ""),
            "email":   lead.get("email", ""),
            "phone":   lead.get("phone", ""),
            "service": " ".join(lead.get("services_required") or []),
            "urgency": lead.get("urgency", ""),
            "emirate": lead.get("emirate", ""),
            "source":  lead.get("source", ""),
        }

        scoring = score_lead(normalized)
        update_lead_score(lead["id"], scoring)

        band = scoring.get("band", "COLD")
        if band == "HOT":    hot += 1
        elif band == "WARM":  warm += 1
        elif band == "MEDIUM": medium += 1
        else:                  cold += 1

        log.info(
            f"  Lead #{lead['id']} — {lead.get('company_name','?')} | "
            f"score={scoring['score']} band={band}"
        )

    log.info(f"\nBackfill complete:")
    log.info(f"  🔥 HOT:    {hot}")
    log.info(f"  🟡 WARM:   {warm}")
    log.info(f"  🔵 MEDIUM: {medium}")
    log.info(f"  ⚪ COLD:   {cold}")
    log.info(f"  Total:     {len(leads)}")


if __name__ == "__main__":
    main()
