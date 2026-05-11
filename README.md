# 🛟 AI Customer Support Agent (LangGraph + OpenAI + Streamlit)

An end-to-end customer-support automation agent that takes a free-text complaint, classifies it, scores its urgency, drafts a response, suggests concrete resolution steps, and decides whether to auto-resolve or escalate to a human team.

## 🧭 Architecture

```
User Complaint
      │
      ▼
┌──────────────┐
│ Classify     │  → Payment / Login / Refund / Bug / General
└──────┬───────┘
       ▼
┌──────────────┐
│ Urgency      │  → LOW / MEDIUM / HIGH / CRITICAL
└──────┬───────┘
       ▼
┌──────────────┐
│ AI Resolution│  → empathetic reply + 3-6 resolution steps
└──────┬───────┘
       ▼
┌──────────────┐
│ Escalation   │  → rule layer (urgency × category × confidence)
│ Decision     │
└──────┬───────┘
       ▼
┌──────────────┐
│ Close /      │  → RESOLVED or ESCALATED
│ Escalate     │
└──────┬───────┘
       ▼
   UI Response
```

Implemented as a **LangGraph** `StateGraph` in `workflow.py`. The Streamlit UI in `app.py` invokes the compiled graph and renders the result.

## 📁 Project structure

```
customer_support_agent/
├── workflow.py        # LangGraph nodes + graph builder
├── app.py             # Streamlit UI
├── requirements.txt
├── .env.example       # copy to .env and add your key
└── README.md
```

## 🚀 Setup

```bash
# 1. Create + activate a virtualenv (optional but recommended)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# then edit .env and set OPENAI_API_KEY=sk-...

# 4. (Optional) sanity-check the graph from CLI
python workflow.py

# 5. Launch the Streamlit UI
streamlit run app.py
```

Open the URL Streamlit prints (usually http://localhost:8501).

## 🧩 Categories handled

| Category         | Examples                                    |
|------------------|---------------------------------------------|
| Payment Issue    | Money deducted but payment failed           |
| Login Problem    | Can't log in, password reset not arriving   |
| Refund Request   | Refund pending beyond SLA                   |
| Technical Bug    | App crash, broken feature                   |
| General Inquiry  | Catch-all (shipping, hours, etc.)           |

## 🚦 Escalation rules (in `escalation_decision`)

These are kept as plain Python so they're easy to tune without prompting:

- `CRITICAL` urgency → always escalate
- `HIGH` + Payment / Refund → Payments team
- `HIGH` + Technical Bug → Engineering on-call
- `HIGH` + Login Problem → Identity & Security
- Classifier confidence < 0.45 → human triage
- Otherwise → auto-resolve

This layer is the **maintainability seam** — product/ops can change routing logic without touching the LLM prompts.

## 🧪 Try the built-in examples

The Streamlit UI ships with example complaints in a dropdown — including the headline "Payment failed but money deducted" scenario. Pick one, hit **Run support agent**, and inspect the trace.

## ⚙️ Configuration

`.env` keys:

| Key             | Default        | Purpose                       |
|-----------------|----------------|-------------------------------|
| `OPENAI_API_KEY`| —              | Required                      |
| `OPENAI_MODEL`  | `gpt-4o-mini`  | Any chat-completions model    |

