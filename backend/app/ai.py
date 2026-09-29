"""
ai.py — AI/NLP integration.

Primary provider: Groq (https://console.groq.com), OpenAI-compatible chat
completions, free tier, no credit card. Set GROQ_API_KEY in the environment
to enable it. Model is configurable via GROQ_MODEL (default
openai/gpt-oss-120b).

Every function here has a deterministic, rule-based fallback so the whole
system still runs end-to-end with zero API keys configured — the brief
requires `docker compose up` to bring up a working system, and AI parsing
degrading to "not as smart" rather than "broken" is the right trade-off.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

import httpx

log = logging.getLogger("ai")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_client = httpx.AsyncClient(timeout=15.0)


async def _groq_json(system_prompt: str, user_prompt: str) -> Optional[dict]:
    """Call Groq and get back a parsed JSON object, or None on any failure
    (missing key, network error, bad JSON) — callers must handle None."""
    if not GROQ_API_KEY:
        return None
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "temperature": 0.2,
        "max_tokens": 600,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        resp = await _client.post(GROQ_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            # Some models on some accounts reject response_format — retry without it.
            payload.pop("response_format", None)
            resp = await _client.post(GROQ_URL, headers=headers, json=payload)
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001 — AI must never crash the simulator
        log.warning("groq call failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Distress message parsing
# ---------------------------------------------------------------------------

DISTRESS_SYSTEM_PROMPT = (
    "You are a maritime distress-call triage system. Extract structured data "
    "from a ship captain's free-form distress message. Respond ONLY with a "
    "JSON object, no prose, with exactly these keys: "
    '{"severity": "info|warning|critical", "issue": "short phrase describing '
    'the problem", "injuries": integer, "damage_estimate_usd": integer, '
    '"summary": "one sentence for a fleet operator"}. '
    "If a field is not mentioned, use 0 for numbers and your best short "
    "phrase for text. Numbers must always be plain integers: convert "
    "spelled-out counts ('two crew injured' -> injuries: 2, 'a couple' -> 2) "
    "and amounts ('half a million' -> 500000) instead of returning 0. Judge severity by real operational risk: fire, "
    "flooding, hull breach, medical emergencies, or ships that are sinking "
    "are critical; engine trouble, minor injuries, or equipment failure are "
    "warning; everything else is info."
)

_KEYWORDS_CRITICAL = ["fire", "sinking", "flooding", "explosion", "hull breach", "abandon ship", "capsiz"]
_KEYWORDS_WARNING = ["engine", "injur", "medical", "leak", "collision", "adrift", "power loss", "grounded"]


def _fallback_parse_distress(raw_message: str) -> dict:
    text = raw_message.lower()
    severity = "info"
    if any(k in text for k in _KEYWORDS_CRITICAL):
        severity = "critical"
    elif any(k in text for k in _KEYWORDS_WARNING):
        severity = "warning"

    injuries_match = re.search(r"(\d+)\s*(injur|hurt|wounded|casualt)", text)
    injuries = int(injuries_match.group(1)) if injuries_match else 0

    damage = 0
    damage_match = re.search(r"\$?\s*(\d[\d,]*)\s*(k|thousand|m|million)?\s*(damage|loss|cost)", text)
    if damage_match:
        num = int(damage_match.group(1).replace(",", ""))
        mult = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "million": 1_000_000}.get(
            (damage_match.group(2) or "").lower(), 1
        )
        damage = num * mult

    issue = raw_message.strip()[:80] or "Unspecified issue"
    return {
        "severity": severity,
        "issue": issue,
        "injuries": injuries,
        "damage_estimate_usd": damage,
        "summary": f"Keyword-triaged as {severity}: {issue}",
        "ai_parsed": False,
    }


_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "couple": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}
_INJURY_RE = re.compile(
    r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")\b(?:\s+\w+){0,2}?\s+(?:injur|hurt|wounded|casualt)"
    r"|(?:injur\w*|casualt\w*)\s*[:=]?\s*(\d+)\b",
    re.IGNORECASE,
)


def _regex_injury_count(text: str) -> int:
    """Safety net for LLM misses: find digit or spelled-out injury counts."""
    best = 0
    for m in _INJURY_RE.finditer(text):
        tok = (m.group(1) or m.group(2) or "").lower()
        n = int(tok) if tok.isdigit() else _NUMBER_WORDS.get(tok, 0)
        best = max(best, n)
    return best


def _to_int(value) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


async def parse_distress(raw_message: str) -> dict:
    result = await _groq_json(DISTRESS_SYSTEM_PROMPT, raw_message)
    if result is None:
        fb = _fallback_parse_distress(raw_message)
        fb["injuries"] = max(fb["injuries"], _regex_injury_count(raw_message))
        return fb
    result.setdefault("severity", "warning")
    result.setdefault("issue", raw_message[:80])
    result.setdefault("injuries", 0)
    result.setdefault("damage_estimate_usd", 0)
    result.setdefault("summary", result.get("issue", ""))
    result["injuries"] = max(_to_int(result["injuries"]), _regex_injury_count(raw_message))
    result["damage_estimate_usd"] = _to_int(result["damage_estimate_usd"])
    result["ai_parsed"] = True
    return result


# ---------------------------------------------------------------------------
# Fleet advisor — proactive suggestions for Command (PDF bonus feature)
# ---------------------------------------------------------------------------

ADVISOR_SYSTEM_PROMPT = (
    "You are a fleet operations advisor for a maritime command center "
    "overseeing 15 cargo ships through the Strait of Hormuz. You are given a "
    "JSON snapshot of current ship states and active alerts. Suggest up to 3 "
    "concrete, prioritized actions Command could take right now. Respond "
    "ONLY with a JSON object: "
    '{"suggestions": [{"action": "short imperative sentence", '
    '"reasoning": "one sentence why", "ship_id": "id or null", '
    '"priority": "low|medium|high"}]}. Be specific and reference ship names '
    "or ids from the data given. If nothing needs attention, return an "
    "empty suggestions list."
)


def _fallback_advisor(fleet_summary: dict, alerts_summary: list) -> dict:
    suggestions = []
    for ship in fleet_summary.get("ships", []):
        if ship.get("fuel_percent", 100) < 20:
            suggestions.append({
                "action": f"Consider diverting {ship['name']} to the nearest port for refuelling",
                "reasoning": f"Fuel at {ship['fuel_percent']:.0f}%, below the 20% safety margin",
                "ship_id": ship["id"],
                "priority": "high" if ship["fuel_percent"] < 10 else "medium",
            })
        if ship.get("status") == "distressed":
            suggestions.append({
                "action": f"Direct nearest available ship to assist {ship['name']}",
                "reasoning": "Ship reported distress and has not been resolved",
                "ship_id": ship["id"],
                "priority": "high",
            })
    critical_alerts = [a for a in alerts_summary if a.get("severity") == "critical" and not a.get("resolved")]
    if critical_alerts and not suggestions:
        suggestions.append({
            "action": "Review unresolved critical alerts in the alert feed",
            "reasoning": f"{len(critical_alerts)} critical alert(s) are still unacknowledged",
            "ship_id": None,
            "priority": "high",
        })
    return {"suggestions": suggestions[:3]}


async def fleet_advisor_suggestions(fleet_summary: dict, alerts_summary: list) -> dict:
    payload = json.dumps({"ships": fleet_summary.get("ships", []), "alerts": alerts_summary[-15:]})
    result = await _groq_json(ADVISOR_SYSTEM_PROMPT, payload)
    if result is None or "suggestions" not in result:
        fallback = _fallback_advisor(fleet_summary, alerts_summary)
        fallback["ai_generated"] = False
        return fallback
    result["ai_generated"] = True
    return result


# ---------------------------------------------------------------------------
# Incident report auto-summary (extra feature, beyond the brief)
# ---------------------------------------------------------------------------

REPORT_SYSTEM_PROMPT = (
    "You write short, factual maritime incident reports for a fleet log. "
    "Given a JSON description of one alert/incident, write a 2-3 sentence "
    "professional incident summary suitable for a shipping company's "
    "records. Respond ONLY with a JSON object: {\"report\": \"...\"}. No "
    "speculation beyond the given facts."
)


def _fallback_report(incident: dict) -> str:
    ship = incident.get("ship_name", "Unknown vessel")
    kind = incident.get("type", "incident").replace("_", " ")
    ts = incident.get("created_at", "")
    return (
        f"Incident log: {ship} — {kind}. {incident.get('message', '')} "
        f"Recorded at {ts}. (Auto-generated without AI enrichment — set "
        f"GROQ_API_KEY for narrative reports.)"
    )


async def generate_incident_report(incident: dict) -> str:
    result = await _groq_json(REPORT_SYSTEM_PROMPT, json.dumps(incident))
    if result is None or "report" not in result:
        return _fallback_report(incident)
    return result["report"]


async def shutdown():
    await _client.aclose()
