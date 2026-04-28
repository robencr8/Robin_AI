"""
ECO Technology — Lead Scoring Engine
======================================
Scores leads 0–100 based on:
  - RAG intent classification (confidence-weighted)
  - Urgency level
  - Company/facility type
  - Service type (revenue potential)
  - Emirate (proximity / market value)
  - Source channel quality

Score bands:
  80–100 → HOT    — contact within 1 hour
  60–79  → WARM   — contact within 2 hours
  40–59  → MEDIUM — Touch 1 email + follow-up within 24h
  0–39   → COLD   — standard pipeline

Always returns a score even if RAG is unavailable (heuristic-only mode).
"""

from __future__ import annotations
import logging
from typing import Optional

log = logging.getLogger(__name__)


# ── Scoring tables ─────────────────────────────────────────────────────────────

URGENCY_SCORES: dict[str, int] = {
    "emergency within 24hrs": 35,
    "urgent within 1 week":   25,
    "planned within 1 month": 10,
    "exploring options":       5,
    "urgent":                 25,
    "planned":                10,
    "exploring":               5,
    "emergency":              35,
}

SERVICE_SCORES: dict[str, int] = {
    "annual maintenance contract":  20,
    "amc":                          20,
    "grease trap supply":           18,
    "grease trap installation":     18,
    "sewage tank desludging":       14,
    "marine vessel":                16,
    "biological treatment":         12,
    "grease trap cleaning":         12,
    "used cooking oil":             10,
    "uco":                          10,
    "high-pressure":                 8,
    "confined space":                8,
}

COMPANY_TYPE_SCORES: dict[str, int] = {
    "mall":          18,
    "hotel":         18,
    "resort":        18,
    "hospital":      16,
    "airport":       16,
    "municipality":  15,
    "authority":     15,
    "hypermarket":   14,
    "supermarket":   14,
    "industrial":    13,
    "factory":       13,
    "restaurant":    10,
    "food":          10,
    "school":         8,
    "residential":    6,
    "office":         5,
}

EMIRATE_SCORES: dict[str, int] = {
    "dubai":           15,
    "abu dhabi":       15,
    "sharjah":         12,
    "ajman":           10,
    "ras al khaimah":   8,
    "rak":              8,
    "fujairah":         8,
    "umm al quwain":    6,
    "uaq":              6,
}

SOURCE_SCORES: dict[str, int] = {
    "website_form":     10,
    "jotform_form":     10,
    "robin_ai_agent":   12,   # Agent-captured leads showed active intent
    "linkedin":          8,
    "instagram":         6,
    "referral":         15,   # Highest quality source
    "direct":           12,
    "google":           10,
    "whatsapp":         11,
}

RAG_CONFIDENCE_MULTIPLIER = 10  # max bonus for high-confidence RAG classification


# ── Scoring functions ──────────────────────────────────────────────────────────

def _score_urgency(urgency: str) -> int:
    if not urgency:
        return 5
    u = urgency.lower().strip()
    for key, score in URGENCY_SCORES.items():
        if key in u:
            return score
    return 5


def _score_service(service: str | list) -> int:
    if not service:
        return 5
    # Handle both string and list
    if isinstance(service, list):
        text = " ".join(str(s) for s in service).lower()
    else:
        text = str(service).lower()

    best = 5
    for key, score in SERVICE_SCORES.items():
        if key in text:
            best = max(best, score)
    return best


def _score_company(company: str) -> int:
    if not company:
        return 5
    text = company.lower()
    best = 5
    for key, score in COMPANY_TYPE_SCORES.items():
        if key in text:
            best = max(best, score)
    return best


def _score_emirate(emirate: str) -> int:
    if not emirate:
        return 5
    e = emirate.lower().strip()
    for key, score in EMIRATE_SCORES.items():
        if key in e:
            return score
    return 5


def _score_source(source: str) -> int:
    if not source:
        return 5
    s = source.lower().strip()
    return SOURCE_SCORES.get(s, 5)


def _rag_bonus(rag_classification: Optional[dict]) -> int:
    """
    Bonus from RAG classification.
    Only "eco" intent leads get a positive bonus.
    Other intents (cv, general) get a slight penalty.
    """
    if not rag_classification or not rag_classification.get("rag_available"):
        return 0
    intent     = rag_classification.get("intent", "eco")
    confidence = float(rag_classification.get("confidence", 0.0))

    if intent == "eco":
        return int(confidence * RAG_CONFIDENCE_MULTIPLIER)
    elif intent == "general":
        return -3   # Lower quality signal
    else:
        return -5   # Mis-routed


# ── Public API ──────────────────────────────────────────────────────────────────

def score_lead(lead: dict, rag_classification: Optional[dict] = None) -> dict:
    """
    Score a lead and return full scoring breakdown.

    Args:
        lead: normalized lead dict {name, company, email, phone, service, emirate, urgency, source}
        rag_classification: output from rag_client.classify_lead() — optional

    Returns:
        {
            score: int (0–100),
            band: "HOT" | "WARM" | "MEDIUM" | "COLD",
            recommended_action: str,
            breakdown: {urgency, service, company, emirate, source, rag_bonus},
            rag_used: bool,
        }
    """
    urgency_score  = _score_urgency(lead.get("urgency", ""))
    service_score  = _score_service(lead.get("service", ""))
    company_score  = _score_company(lead.get("company", ""))
    emirate_score  = _score_emirate(lead.get("emirate", ""))
    source_score   = _score_source(lead.get("source", ""))
    rag_bonus      = _rag_bonus(rag_classification)

    raw_score = (
        urgency_score
        + service_score
        + company_score
        + emirate_score
        + source_score
        + rag_bonus
    )

    # Clamp 0–100
    score = max(0, min(100, raw_score))

    # Band assignment
    if score >= 80:
        band   = "HOT"
        action = "Call within 1 hour — high-value lead"
    elif score >= 60:
        band   = "WARM"
        action = "Call within 2 hours — strong buying signal"
    elif score >= 40:
        band   = "MEDIUM"
        action = "Touch 1 email sent — follow up within 24h"
    else:
        band   = "COLD"
        action = "Standard pipeline — monitor response"

    result = {
        "score":              score,
        "band":               band,
        "recommended_action": action,
        "rag_used":           bool(rag_classification and rag_classification.get("rag_available")),
        "breakdown": {
            "urgency":    urgency_score,
            "service":    service_score,
            "company":    company_score,
            "emirate":    emirate_score,
            "source":     source_score,
            "rag_bonus":  rag_bonus,
        },
    }

    log.info(
        f"Lead scored: {lead.get('company','?')} | "
        f"score={score} band={band} | "
        f"urgency={urgency_score} service={service_score} "
        f"company={company_score} emirate={emirate_score} "
        f"source={source_score} rag={rag_bonus}"
    )

    return result
