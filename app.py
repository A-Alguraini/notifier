import os
import sys
import requests
from flask import Flask, request
import resend
from dotenv import load_dotenv

# ── Setup ─────────────────────────────────────────────────────────────────────
load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
if not RESEND_API_KEY:
    raise RuntimeError("Missing RESEND_API_KEY")
resend.api_key = RESEND_API_KEY

# Ensure OPENMETER_BASE always has /api/v1
_raw_base = os.getenv("OPENMETER_BASE", "https://openmeter.cloud/api/v1").rstrip("/")
if not _raw_base.endswith("/api/v1"):
    _raw_base = _raw_base + "/api/v1"
OPENMETER_BASE = _raw_base

OPENMETER_API_KEY = os.getenv("OPENMETER_API_KEY", "")
if not OPENMETER_API_KEY:
    raise RuntimeError("Missing OPENMETER_API_KEY")

FROM_EMAIL = "Nabrah <no-reply@nabrah.ai>"
FALLBACK_EMAIL = os.getenv("FALLBACK_EMAIL", "aalguraini@dscan.ai")

app = Flask(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────
def _om_headers():
    return {
        "Authorization": f"Bearer {OPENMETER_API_KEY}",
        "Accept": "application/json",
    }

def _safe_json(resp: requests.Response):
    """Parse JSON or return None with a short diagnostic print."""
    try:
        return resp.json()
    except Exception:
        ctype = resp.headers.get("Content-Type", "")
        preview = (resp.text or "")[:300]
        print(
            f"⚠️ Non-JSON from {resp.url} [{resp.status_code}] "
            f"CT={ctype} body[:300]={preview!r}",
            flush=True,
        )
        return None

def get_customer_from_openmeter(subject_key: str):
    """
    Look up a customer by the subject key:
      1) GET /customers/{subject_key_without_hyphens}
      2) GET /customers?subjectKey={subject_key}
    Returns a dict (customer) or None. Never raises.
    """
    try:
        # 1) direct by key (hyphens removed)
        customer_key = subject_key.replace("-", "")
        url1 = f"{OPENMETER_BASE}/customers/{customer_key}"
        r1 = requests.get(url1, headers=_om_headers(), timeout=10)
        print(f"🔎 OpenMeter GET {url1} -> {r1.status_code}", flush=True)

        if r1.status_code == 200:
            body = _safe_json(r1)
            if isinstance(body, dict) and body.get("key"):
                return body

        # 2) fallback by subjectKey
        url2 = f"{OPENMETER_BASE}/customers"
        r2 = requests.get(url2, headers=_om_headers(),
                          params={"subjectKey": subject_key}, timeout=10)
        print(f"🔎 OpenMeter GET {url2}?subjectKey={subject_key} -> {r2.status_code}",
              flush=True)

        if r2.status_code == 200:
            body2 = _safe_json(r2)
            # API may return a single object or a list; handle both
            if isinstance(body2, dict) and body2.get("key"):
                return body2
            if isinstance(body2, list) and body2:
                return body2[0]

    except Exception as e:
        print(f"⚠️ OpenMeter lookup error: {e}", flush=True)

    return None

def pick_project_name_and_email(customer: dict):
    """Extract a display project name and recipient email from a customer dict."""
    if not isinstance(customer, dict):
        return "Your Project", FALLBACK_EMAIL

    # Project (customer) name
    name = customer.get("name") or "Your Project"

    # Email preference: primaryEmail → metadata.email → fallback
    email = (
        customer.get("primaryEmail")
        or (customer.get("metadata") or {}).get("email")
        or FALLBACK_EMAIL
    )

    return name, email

def minutes_left_from_payload(data: dict) -> float:
    """Return minutes left from the webhook 'value.balance' (or 0.0)."""
    try:
        return float(((data or {}).get("value") or {}).get("balance") or 0.0)
    except Exception:
        return 0.0

# ── Webhook ───────────────────────────────────────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    print(f"📥 Received webhook: {data}", flush=True)

    event_type = data.get("type")

    # Optional: handle "Send Test" events if you use them
    if event_type == "notification.test":
        print("🧪 Test event received — sending a test email…", flush=True)
        try:
            resend.Emails.send({
                "from": FROM_EMAIL,
                "to": [FALLBACK_EMAIL],
                "subject": "✅ OpenMeter Test Email",
                "text": "This is a test email from OpenMeter.",
            })
            print("📨 Test email sent!", flush=True)
        except Exception as e:
            print(f"❌ Failed to send test email: {e}", flush=True)
        return "", 200

    if event_type != "entitlements.balance.threshold":
        print(f"⚪ Ignored event type: {event_type}", flush=True)
        return "", 200

    # Parse essentials
    try:
        subject_key = ((data.get("data") or {}).get("subject") or {}).get("key")
        feature     = ((data.get("data") or {}).get("feature") or {}).get("name") or "Call Minutes"
        threshold   = ((data.get("data") or {}).get("threshold") or {}).get("value")
        if isinstance(threshold, dict) and "value" in threshold:  # overly defensive
            threshold = threshold.get("value")

        # Nice log line
        print(
            f"🧩 Parsed -> subject_key={subject_key}, feature={feature}, threshold={threshold}%",
            flush=True,
        )
    except Exception as e:
        print(f"❌ Could not parse payload: {e}", flush=True)
        return "", 200

    # Look up customer for recipient & project name
    customer = get_customer_from_openmeter(subject_key) if subject_key else None
    project_name, email_to = pick_project_name_and_email(customer)

    if customer:
        print(f"✅ Resolved customer: name={project_name!r}, email={email_to!r}", flush=True)
    else:
        print(f"🟡 No customer found; using fallback email {email_to}", flush=True)

    # Minutes left
    minutes_left = minutes_left_from_payload(data.get("data") or {})
    minutes_left_str = f"{minutes_left:.2f}"

    # Build email
    subject = f"Your Project: You’ve reached {threshold}% of your {feature} quota — {minutes_left_str} min left"
    text = (
        f"Hello,\n\n"
        f"Project: {project_name}\n"
        f"{feature} left: {minutes_left_str} min\n\n"
        f"You’ve reached {threshold}% of your {feature} quota for this project.\n"
        f"Consider upgrading for more capacity.\n\n"
        f"— The Nabrah Team"
    )

    try:
        print(f"✉️  Sending alert email → to={email_to} | subject={subject!r}", flush=True)
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": [email_to],
            "subject": subject,
            "text": text,
        })
        print("✅ Alert email sent.", flush=True)
    except Exception as e:
        print(f"❌ Failed to send alert email: {e}", flush=True)

    return "", 200

# ── Local run ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
