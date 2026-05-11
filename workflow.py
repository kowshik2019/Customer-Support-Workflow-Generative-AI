"""
workflow.py
-----------
Customer Support Agent built with LangGraph + OpenAI.

Architecture flow:
    User Complaint -> Classification -> Urgency Detection -> AI Resolution
                   -> Close or Escalate -> UI Response

Nodes:
    1. classify_ticket       - puts the complaint into a category
    2. detect_urgency        - LOW / MEDIUM / HIGH / CRITICAL
    3. generate_response     - drafts an empathetic reply + resolution steps
    4. escalation_decision   - decides auto-resolve vs escalate to human
    5. close_or_escalate     - terminal node that finalizes the ticket
"""

import os
import json
from typing import TypedDict, Literal, List, Optional
from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from openai import OpenAI

# ---------------------------------------------------------------------------
# 0. ENV + CLIENT
# ---------------------------------------------------------------------------
load_dotenv(override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

if not OPENAI_API_KEY:
    # We don't raise here because Streamlit imports this file at startup.
    # The UI will check and show a friendly error.
    print("[WARN] OPENAI_API_KEY not set. Set it in your .env file.")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# ---------------------------------------------------------------------------
# 1. STATE DEFINITION
# ---------------------------------------------------------------------------
CATEGORY = Literal[
    "Payment Issue",
    "Login Problem",
    "Refund Request",
    "Technical Bug",
    "General Inquiry",
]

URGENCY = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class TicketState(TypedDict, total=False):
    # input
    complaint: str
    customer_id: Optional[str]

    # produced by nodes
    category: CATEGORY
    category_confidence: float
    urgency: URGENCY
    urgency_reason: str
    response: str
    resolution_steps: List[str]
    escalate: bool
    escalation_reason: str
    status: Literal["RESOLVED", "ESCALATED"]
    trace: List[str]  # human-readable log of node visits


# ---------------------------------------------------------------------------
# 2. LLM HELPER
# ---------------------------------------------------------------------------
def _call_llm_json(system: str, user: str) -> dict:
    """Call OpenAI in JSON mode and return a parsed dict. Defensive on errors."""
    if client is None:
        raise RuntimeError("OPENAI_API_KEY missing. Add it to .env.")

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = resp.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # extremely defensive fallback
        return {}


def _append_trace(state: TicketState, msg: str) -> List[str]:
    trace = list(state.get("trace", []))
    trace.append(msg)
    return trace


# ---------------------------------------------------------------------------
# 3. NODE: CLASSIFICATION
# ---------------------------------------------------------------------------
def classify_ticket(state: TicketState) -> TicketState:
    """
    Classify a complaint into one of:
    Payment Issue / Login Problem / Refund Request / Technical Bug / General Inquiry
    """
    system = (
        "You are a customer-support ticket classifier. "
        "Read the complaint and pick exactly ONE category from this list: "
        "['Payment Issue', 'Login Problem', 'Refund Request', 'Technical Bug', 'General Inquiry']. "
        "If nothing fits clearly, default to 'General Inquiry'. "
        "Return JSON: {\"category\": <one of the 5>, \"confidence\": <0.0-1.0>}."
    )
    user = f"Complaint:\n\"\"\"{state['complaint']}\"\"\""

    result = _call_llm_json(system, user)
    category = result.get("category", "General Inquiry")
    confidence = float(result.get("confidence", 0.5))

    valid = {
        "Payment Issue",
        "Login Problem",
        "Refund Request",
        "Technical Bug",
        "General Inquiry",
    }
    if category not in valid:
        category = "General Inquiry"
        confidence = 0.4

    return {
        **state,
        "category": category,
        "category_confidence": confidence,
        "trace": _append_trace(state, f"Classified as {category} (conf={confidence:.2f})"),
    }


# ---------------------------------------------------------------------------
# 4. NODE: URGENCY DETECTION
# ---------------------------------------------------------------------------
def detect_urgency(state: TicketState) -> TicketState:
    """
    Heuristic + LLM hybrid:
      - financial harm (money deducted, double-charge, fraud) -> HIGH/CRITICAL
      - account locked, can't log in repeatedly -> HIGH
      - bug blocking core flow -> MEDIUM/HIGH
      - general questions -> LOW
    """
    system = (
        "You assess support-ticket urgency. "
        "Return JSON: {\"urgency\": one of ['LOW','MEDIUM','HIGH','CRITICAL'], \"reason\": <short string>}.\n"
        "Rules:\n"
        "- CRITICAL: money lost / fraud / data breach / security risk / repeated failures with financial impact.\n"
        "- HIGH: payment failed but money deducted, account locked, refund stuck > 7 days, blocker for paid user.\n"
        "- MEDIUM: feature broken with workaround, login intermittently failing, refund recently requested.\n"
        "- LOW: general question, how-to, feedback, no financial or access impact."
    )
    user = (
        f"Category: {state.get('category', 'General Inquiry')}\n"
        f"Complaint: \"\"\"{state['complaint']}\"\"\""
    )

    result = _call_llm_json(system, user)
    urgency = result.get("urgency", "LOW")
    reason = result.get("reason", "Default urgency assigned.")

    if urgency not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        urgency = "LOW"

    return {
        **state,
        "urgency": urgency,
        "urgency_reason": reason,
        "trace": _append_trace(state, f"Urgency = {urgency} ({reason})"),
    }


# ---------------------------------------------------------------------------
# 5. NODE: AI RESOLUTION (Generate Response + Suggest Resolutions)
# ---------------------------------------------------------------------------
def generate_response(state: TicketState) -> TicketState:
    """
    Produces:
      - empathetic customer-facing message
      - 3-6 concrete resolution steps the customer (or agent) can take
    Tailors content to the category.
    """
    category = state.get("category", "General Inquiry")
    urgency = state.get("urgency", "LOW")

    system = (
        "You are a senior customer-support agent. Reply with empathy, brevity, and a clear plan. "
        "Output JSON with two fields:\n"
        "  - 'response': a 2-4 sentence message to the customer (warm, no jargon, sets expectations).\n"
        "  - 'resolution_steps': a list of 3 to 6 concrete steps to resolve the issue.\n"
        "Tailor the steps to the ticket category. For 'Payment Issue' where money was deducted but "
        "payment failed, mention typical bank settlement windows (5-7 business days), a reference/transaction ID "
        "request, and an offer to raise a chargeback dispute if the amount isn't auto-reversed."
    )
    user = (
        f"Category: {category}\n"
        f"Urgency: {urgency}\n"
        f"Complaint: \"\"\"{state['complaint']}\"\"\""
    )

    result = _call_llm_json(system, user)
    response_text = result.get("response", "Thanks for reaching out. We're looking into this.")
    steps = result.get("resolution_steps") or []
    if not isinstance(steps, list):
        steps = [str(steps)]
    steps = [str(s).strip() for s in steps if str(s).strip()]
    if not steps:
        steps = ["Acknowledge the issue", "Gather details", "Investigate", "Follow up with the customer"]

    return {
        **state,
        "response": response_text,
        "resolution_steps": steps,
        "trace": _append_trace(state, f"Generated response with {len(steps)} resolution steps"),
    }


# ---------------------------------------------------------------------------
# 6. NODE: ESCALATION DECISION
# ---------------------------------------------------------------------------
def escalation_decision(state: TicketState) -> TicketState:
    """
    Maintainable rule layer. Easy to tweak without touching the LLM:
      - CRITICAL                  -> always escalate
      - HIGH + Payment/Refund     -> escalate (financial)
      - HIGH + Technical Bug      -> escalate (engineering)
      - HIGH + Login Problem      -> escalate (security/identity)
      - low classifier confidence -> escalate to human triage
      - everything else           -> auto-resolve
    """
    urgency = state.get("urgency", "LOW")
    category = state.get("category", "General Inquiry")
    confidence = state.get("category_confidence", 1.0)

    escalate = False
    reason = "Auto-resolved by AI agent."

    if urgency == "CRITICAL":
        escalate = True
        reason = "Critical urgency — must reach a human agent immediately."
    elif urgency == "HIGH" and category in {"Payment Issue", "Refund Request"}:
        escalate = True
        reason = "High-urgency financial issue — routing to Payments team."
    elif urgency == "HIGH" and category == "Technical Bug":
        escalate = True
        reason = "High-urgency technical bug — routing to Engineering on-call."
    elif urgency == "HIGH" and category == "Login Problem":
        escalate = True
        reason = "High-urgency access issue — routing to Identity & Security team."
    elif confidence < 0.45:
        escalate = True
        reason = f"Low classifier confidence ({confidence:.2f}) — sending to human triage."

    return {
        **state,
        "escalate": escalate,
        "escalation_reason": reason,
        "trace": _append_trace(state, f"Escalate={escalate} :: {reason}"),
    }


# ---------------------------------------------------------------------------
# 7. NODE: CLOSE OR ESCALATE (terminal)
# ---------------------------------------------------------------------------
def close_or_escalate(state: TicketState) -> TicketState:
    status = "ESCALATED" if state.get("escalate") else "RESOLVED"
    return {
        **state,
        "status": status,
        "trace": _append_trace(state, f"Final status: {status}"),
    }


# ---------------------------------------------------------------------------
# 8. BUILD THE GRAPH
# ---------------------------------------------------------------------------
def build_graph():
    g = StateGraph(TicketState)

    g.add_node("classify", classify_ticket)
    g.add_node("urgency", detect_urgency)
    g.add_node("respond", generate_response)
    g.add_node("decide", escalation_decision)
    g.add_node("finalize", close_or_escalate)

    g.set_entry_point("classify")
    g.add_edge("classify", "urgency")
    g.add_edge("urgency", "respond")
    g.add_edge("respond", "decide")
    g.add_edge("decide", "finalize")
    g.add_edge("finalize", END)

    return g.compile()


# Compiled graph singleton for the UI
SUPPORT_GRAPH = build_graph() if OPENAI_API_KEY else None


def run_ticket(complaint: str, customer_id: Optional[str] = None) -> TicketState:
    """Convenience wrapper used by the Streamlit UI."""
    if SUPPORT_GRAPH is None:
        raise RuntimeError("Graph not initialized. Set OPENAI_API_KEY in .env and restart.")

    initial: TicketState = {
        "complaint": complaint,
        "customer_id": customer_id,
        "trace": [],
    }
    final_state: TicketState = SUPPORT_GRAPH.invoke(initial)
    return final_state


# ---------------------------------------------------------------------------
# 9. CLI sanity check
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sample = (
        "I tried to pay for my order today, the payment page said FAILED but "
        "₹2,499 was deducted from my bank account. This is the second time. "
        "Please refund immediately!"
    )
    out = run_ticket(sample, customer_id="CUS-123")
    print(json.dumps(out, indent=2))
