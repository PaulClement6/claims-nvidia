"""The scripted claim agent: a state machine that reaches data only through the gateway.

It imports no store (tested). State, kept on the claim:
ask_story -> ask_date -> ask_amount -> ask_documents -> ready_for_checks.
"""

import re
from datetime import date, datetime
from typing import Any

from app.tools.gateway import Gateway

DATE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|\d{1,2}(?:st|nd|rd|th)?(?: of)? [A-Za-z]+,? \d{4}"
    r"|[A-Za-z]+ \d{1,2}(?:st|nd|rd|th)?,? \d{4}"
)
# ponytail: day-first for dotted and slashed dates (Swiss and European customers);
# 03/08/2026 is 3 August. A month-first customer needs a locale on the claim.
DATE_FORMATS = [
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d %Y",
    "%b %d %Y",
]
NUM = r"\d[\d'’,.]*"
AMOUNT_RE = re.compile(rf"(?:CHF|Fr\.?)\s*({NUM})|({NUM})\s*(?:CHF|francs)", re.I)
ILL_RE = re.compile(r"\b(ill|sick|illness)\b", re.I)
LUGGAGE_RE = re.compile(r"\b(luggage|baggage)\b", re.I)

TYPE_NAMES = {
    "booking_confirmation": "a booking confirmation",
    "cancellation_confirmation": "a cancellation confirmation",
    "medical_certificate": "a medical certificate",
    "receipt": "a receipt",
}


def parse_date(text: str) -> date | None:
    for m in DATE_RE.finditer(text):
        raw = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", m.group()).replace(" of ", " ")
        raw = raw.replace(",", "")
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                pass
    return None


def parse_amount(text: str, bare_ok: bool) -> float | None:
    m = AMOUNT_RE.search(text)
    raw = (m.group(1) or m.group(2)) if m else None
    if raw is None and bare_ok and re.fullmatch(NUM, text.strip()):
        raw = text.strip()
    if raw is None:
        return None
    raw = raw.replace("'", "").replace("’", "").rstrip(".,")
    # "1,500" is a thousands comma; "1,5" or "1500,50" is a decimal comma
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", raw):
        raw = raw.replace(",", "")
    raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_cause(story: str) -> str:
    if ILL_RE.search(story):
        return "illness"
    if LUGGAGE_RE.search(story):
        return "luggage"
    return "cancellation"


class ScriptedAgent:
    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway

    def _call(self, claim_id: str, tool: str, **inputs: Any) -> dict[str, Any]:
        return self.gateway.call(claim_id, tool, {"claim_id": claim_id, **inputs})

    def handle(
        self, claim_id: str, text: str | None, document_id: str | None
    ) -> list[str]:
        s = self._call(claim_id, "get_claim_summary")
        out: list[str] = []
        changes: dict[str, Any] = {}
        if document_id:
            out.append(self._read_document(claim_id, document_id))
            s = self._call(claim_id, "get_claim_summary")
        elif text is None:
            out.append(
                f"Hello {s['customer_name']}. I will help you with your claim on "
                f"policy {s['policy_number']}."
            )
        elif (
            s["agent_state"] == "ask_documents" and text.strip(" .!").lower() == "done"
        ):
            self._call(claim_id, "update_claim", agent_state="ready_for_checks")
            return ["Thank you. I have what I need from you."]
        elif s["agent_state"] != "ready_for_checks":
            asked = s["agent_state"]
            changes = self._fill(s, text)
            if asked == "ask_date" and "incident_date" not in changes:
                out.append("I could not read the date.")
            if asked == "ask_amount" and "claimed_amount" not in changes:
                out.append("I could not read the amount.")
            if changes:
                self._call(claim_id, "update_claim", **changes)
            s = {**s, **changes}
        else:
            return ["Thank you. I have your claim and I will come back to you."]

        missing = self._missing(claim_id, s)
        state = self._state(s, missing)
        if state != s["agent_state"]:
            self._call(claim_id, "update_claim", agent_state=state)
        out.append(self._question(state, missing))
        return [" ".join(out)]  # one chat message per turn

    def _fill(self, s: dict[str, Any], text: str) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if not s["description"]:
            changes["description"] = text.strip()
            changes["cause"] = parse_cause(text)
        if not s["incident_date"] and (d := parse_date(text)):
            changes["incident_date"] = d.isoformat()
        known = changes.get("incident_date") or s["incident_date"]
        if not s["claimed_amount"] and (
            a := parse_amount(text, bare_ok=bool(s["description"] and known))
        ):
            changes["claimed_amount"] = a
        return changes

    def _read_document(self, claim_id: str, document_id: str) -> str:
        ids = {"document_id": document_id}
        self.gateway.call(claim_id, "classify_document", ids)
        kind = self.gateway.call(claim_id, "extract_document", ids)["document_type"]
        if kind == "unknown":
            return "I could not recognise this document."
        name = TYPE_NAMES.get(kind, kind.replace("_", " "))
        return f"Thank you. I read this as {name}."

    def _missing(self, claim_id: str, s: dict[str, Any]) -> list[str]:
        required = self._call(claim_id, "required_documents")["documents"]
        have = set(s["document_types"])
        return [r["label"] for r in required if not have & set(r["any_of"])]

    def _state(self, s: dict[str, Any], missing: list[str]) -> str:
        if s["agent_state"] == "ready_for_checks":
            return "ready_for_checks"
        for slot, state in (
            ("description", "ask_story"),
            ("incident_date", "ask_date"),
            ("claimed_amount", "ask_amount"),
        ):
            if not s[slot]:
                return state
        return "ask_documents" if missing else "ready_for_checks"

    def _question(self, state: str, missing: list[str]) -> str:
        if state == "ask_story":
            return "What happened? Please tell me in your own words."
        if state == "ask_date":
            return "On what date did it happen? For example: 3 August 2026."
        if state == "ask_amount":
            return "What amount do you claim, in CHF? For example: CHF 1,500."
        if state == "ask_documents":
            return (
                f"Please upload the documents I still need: {', and '.join(missing)}. "
                "You can add them one by one. If you have nothing more, type done."
            )
        return "I have everything I need from you."
