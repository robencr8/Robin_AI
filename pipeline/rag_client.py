"""
ECO Technology — RAG Intelligence Client
=========================================
Thin client for the RAG backend (/api/dispatch, /api/query).

Designed for graceful degradation:
  - If RAG_BASE_URL is not set → skip silently
  - If RAG is unreachable → skip silently, pipeline continues
  - If RAG responds → enrich lead with intent + score + proposal draft

RAG backend contract (confirmed from main.py):
  POST /api/dispatch  {question: str}
  → {capability, kind, status, request_id,
     payload: {intent, confidence, method, question}}

  POST /api/query  {question: str}
  → {answer, sources, ...}

  GET /api/health
  → {status, pipeline_ready, version, chat_model, index_count}
"""

import logging
import os
from typing import Optional
from urllib.request import urlopen, Request as URLRequest
from urllib.error import URLError, HTTPError
import json

log = logging.getLogger(__name__)

RAG_BASE_URL    = os.environ.get("RAG_BASE_URL", "").rstrip("/")
RAG_TIMEOUT_SEC = int(os.environ.get("RAG_TIMEOUT_SEC", "5"))
RAG_API_KEY     = os.environ.get("RAG_API_KEY", "")   # optional bearer token


def _post(path: str, body: dict) -> Optional[dict]:
    """
    POST to RAG backend. Returns parsed JSON or None on any failure.
    Never raises — all exceptions are logged and swallowed.
    """
    if not RAG_BASE_URL:
        return None
    url = f"{RAG_BASE_URL}{path}"
    payload = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if RAG_API_KEY:
        headers["Authorization"] = f"Bearer {RAG_API_KEY}"
    try:
        req = URLRequest(url, data=payload, headers=headers, method="POST")
        with urlopen(req, timeout=RAG_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        log.warning(f"RAG {path} HTTP {e.code}: {e.reason}")
        return None
    except URLError as e:
        log.warning(f"RAG {path} unreachable: {e.reason}")
        return None
    except Exception as e:
        log.warning(f"RAG {path} failed: {e}")
        return None


def _get(path: str) -> Optional[dict]:
    """GET from RAG backend. Returns parsed JSON or None on any failure."""
    if not RAG_BASE_URL:
        return None
    url = f"{RAG_BASE_URL}{path}"
    headers = {}
    if RAG_API_KEY:
        headers["Authorization"] = f"Bearer {RAG_API_KEY}"
    try:
        req = URLRequest(url, headers=headers, method="GET")
        with urlopen(req, timeout=RAG_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning(f"RAG GET {path} failed: {e}")
        return None


# ── Public API ──────────────────────────────────────────────────────────────────

def rag_health() -> dict:
    """
    Check RAG backend health.
    Returns: {available: bool, pipeline_ready: bool, index_count: int|None}
    """
    result = _get("/api/health")
    if result is None:
        return {"available": False, "pipeline_ready": False, "index_count": None}
    return {
        "available": True,
        "pipeline_ready": result.get("pipeline_ready", False),
        "index_count": result.get("index_count"),
        "version": result.get("version"),
    }


def classify_lead(lead: dict) -> dict:
    """
    Classify a lead's intent using RAG /api/dispatch.

    Builds a natural language question from lead fields and routes it.

    Returns:
      {
        rag_available: bool,
        intent: str,          # "eco" | "cv" | "general"
        capability: str,      # "eco" | "cv" | "chat"
        confidence: float,
        method: str,
      }
    Default (RAG unavailable): intent="eco", confidence=0.0
    """
    # Build a natural question from lead data
    name     = lead.get("name", "A client")
    company  = lead.get("company", "their company")
    service  = lead.get("service", "environmental services")
    emirate  = lead.get("emirate", "UAE")
    urgency  = lead.get("urgency", "")

    question = (
        f"{name} from {company} in {emirate} is enquiring about {service}. "
        f"Urgency: {urgency or 'not specified'}. "
        "What is the appropriate ECO Technology service response?"
    )

    result = _post("/api/dispatch", {"question": question})

    if result is None:
        return {
            "rag_available": False,
            "intent": "eco",
            "capability": "eco",
            "confidence": 0.0,
            "method": "heuristic_fallback",
        }

    payload = result.get("payload", {})
    return {
        "rag_available": True,
        "intent":       payload.get("intent", "eco"),
        "capability":   result.get("capability", "eco"),
        "confidence":   float(payload.get("confidence", 0.0)),
        "method":       payload.get("method", "unknown"),
    }


def generate_proposal_context(lead: dict) -> Optional[str]:
    """
    Use RAG /api/query to generate a proposal context for a lead.
    Returns a string context to enrich the proposal, or None if unavailable.

    This is NOT a full proposal — it's RAG-grounded context that the
    Touch 1 email or proposal generator can use.
    """
    service  = lead.get("service", "grease trap cleaning")
    company  = lead.get("company", "your facility")
    emirate  = lead.get("emirate", "UAE")
    urgency  = lead.get("urgency", "")

    question = (
        f"What is the most relevant ECO Technology service and compliance "
        f"information for a {company} in {emirate} requiring {service}?"
        + (f" Urgency: {urgency}." if urgency else "")
    )

    result = _post("/api/query", {"question": question})

    if result is None:
        return None

    answer = result.get("answer") or result.get("response") or result.get("text")
    return str(answer).strip() if answer else None


def forward_to_jotform_agent(raw_payload: dict) -> Optional[dict]:
    """
    Forward a raw Jotform payload to the RAG backend's Jotform ingestion endpoint.
    This indexes the lead into the RAG memory for future retrieval.

    Returns response dict or None on failure.
    Note: indexed=False in response means file-based storage, requires index rebuild.
    """
    result = _post("/api/webhooks/jotform-agent", raw_payload)
    if result:
        log.info(
            f"RAG Jotform ingestion: lead_id={result.get('lead_id')} "
            f"intent={result.get('intent')} indexed={result.get('indexed')}"
        )
    return result
