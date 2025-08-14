import os
import sys
import math
import requests
from flask import Flask, request
import resend
from dotenv import load_dotenv

# ── Setup ─────────────────────────────────────────────────────────────────────
load_dotenv()

resend_api_key = os.getenv("RESEND_API_KEY")
if not resend_api_key:
    raise RuntimeError("Missing RESEND_API_KEY in environment")

openmeter_api_key = os.getenv("OPENMETER_API_KEY")
if not openmeter_api_key:
    raise RuntimeError("Missing OPENMETER_API_KEY in environment")

# Fallback address if customer email is missing
FALLBACK_EMAIL = os.getenv("ALERT_FALLBACK_EMAIL", "aalguraini@dscan.ai")

resend.api_key = resend_api_key

OPENMETER_BASE = "https://openmeter.cloud/api/v1"

app = Flask(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────
def om_get_customer(customer_key: str) -> dict | None:
    """GET /customers/{customer_key} (customer_key = subject_key without dashes)."""
    url = f"{OPENMETER_BASE}/customers/{customer_key}"
    try:
        r = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {openmeter_api_key}",
            },
            timeout=15,
        )
        print(f"🔎 OpenMeter GET {url} -> {r.status_code}", flush=True)
        if r.ok:
            return r.json()
        else:
            print(f"⚠️  OpenMeter lookup failed: {r.status_code} {r.text}", flush=True)
    except Exception as e:
        print(f"❌ OpenMeter request error: {e}", flush=True)
    return None


def first_email_from_customer(cust: dict | None) -> tuple[str, str]:
    """
    Pick an email from OpenMeter customer:
      1) customer.primaryEmail
      2) customer.metadata.email
      3) FALLBACK_EMAIL
    Returns (email, source)
    """
    if not cust:
        return FALLBACK_EMAIL, "fallback(no customer)"
    if cust.get("primaryEmail"):
        return cust["primaryEmail"], "primaryEmail"
    meta = cust.get("metadata") or {}
    if isinstance(meta, dict) and meta.get("email"):
        return meta["email"], "metadata.email"
    return FALLBACK_EMAIL, "fallback(no email)"


def project_name_from(customer: dict | None, subject: dict | None) -> str:
    """
    Best effort project name:
      - customer.name
      - subject.displayName
      - trimmed subject id
    """
    if customer and customer.get("name"):
        return str(customer["name"]).strip()
    if subject and subject.get("displayName"):
        return str(subject["displayName"]).strip()
    if subject and subject.get("id"):
        return str(subject["id"])[:12] + "…"  # short id
    return "Your Project"


def format_minutes_left(x: float | int | None) -> str:
    if x is None:
        return "unknown"
    # Avoid ugly long floats; show whole minutes if close
    whole = int(round(x))
    if math.isclose(whole, x, rel_tol=0, abs_tol=0.01):
        return f"{whole} min"
    return f"{x:.2f} min"


# ── Webhook ───────────────────────────────────────────────────────────────────
@app.route("/", methods=["POST"])
def handle_openmeter():
    event = request.get_json(force=True)
    print("📬 Received webhook:", event, flush=True)

    etype = event.get("type")
    if etype != "entitlements.balance.threshold":
        print(f"ℹ️  Ignored event type: {etype}", flush=True)
        return "", 200

    data = event.get("data") or {}
    feature = (data.get("feature") or {}).get("name") or (data.get("feature") or {}).get("key") or "Unknown feature"
    meter   = (data.get("feature") or {}).get("meterSlug") or "Unknown meter"
    subject = data.get("subject") or {}
    threshold_val = (data.get("threshold") or {}).get("value")
    balance_left  = (data.get("value") or {}).get("balance")  # minutes remaining

    # Subject key from payload, convert to customer key (remove dashes)
    subject_key = subject.get("id") or subject.get("key") or ""
    customer_key = subject_key.replace("-", "") if subject_key else ""
    print(f"🔗 Resolving email via key → subject_key={subject_key} → customer_key={customer_key}", flush=True)

    customer = om_get_customer(customer_key) if customer_key else None
    to_email, src = first_email_from_customer(customer)
    proj_name = project_name_from(customer, subject)
    minutes_left_text = format_minutes_left(balance_left)

    # Email content
    subject_line = f"📈 {proj_name}: You’ve reached {threshold_val}% of your Call Minutes quota — {minutes_left_text} left"
    body_text = (
        f"Hello,\n\n"
        f"Project: {proj_name}\n"
        f"Call Minutes left: {minutes_left_text}\n\n"
        f"You’ve reached {threshold_val}% of your Call Minutes quota for this project.\n"
        f"Consider upgrading for more capacity.\n\n"
        f"— The Nabrah Team"
    )

    print(f"📣 Threshold fired: feature='{feature}', meter='{meter}', threshold={threshold_val}%, minutes_left={balance_left}", flush=True)
    print(f"📧 Using {src}: {to_email}", flush=True)

    try:
        resend.Emails.send({
            "from": "Nabrah <no-reply@nabrah.ai>",
            "to":   [to_email],
            "subject": subject_line,
            "text":    body_text,
        })
        print(f"✅ Alert email sent to {to_email} | subject=“{subject_line}”", flush=True)
    except Exception as e:
        print("❌ Failed to send alert email:", e, flush=True)

    return "", 200


# ── Local run ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Render/Gunicorn handles prod. This is only for local testing.
    app.run(host="0.0.0.0", port=5000)
