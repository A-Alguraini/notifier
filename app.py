# app.py
import os
import requests
from flask import Flask, request
import resend
from dotenv import load_dotenv

# ── Config ─────────────────────────────────────────────────────────
load_dotenv()

# Resend
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
if not RESEND_API_KEY:
    raise RuntimeError("Missing RESEND_API_KEY")
resend.api_key = RESEND_API_KEY
SENDER = "Nabrah <no-reply@nabrah.ai>"

# OpenMeter
OPENMETER_API_KEY = os.getenv("OPENMETER_API_KEY")
if not OPENMETER_API_KEY:
    raise RuntimeError("Missing OPENMETER_API_KEY")

OPENMETER_BASE_URL = "https://openmeter.cloud/api/v1"   # Cloud base URL
FALLBACK_TO = "aalguraini@dscan.ai"                     # safety net

# HTTP defaults
HTTP_TIMEOUT = 10
SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {OPENMETER_API_KEY}",
    "Accept": "application/json",
})

app = Flask(__name__)

# ── Helpers ────────────────────────────────────────────────────────
def fetch_email_from_openmeter(subject_key: str) -> str | None:
    """GET /customers/{subject_key} and return primaryEmail."""
    try:
        url = f"{OPENMETER_BASE_URL}/customers/{subject_key}"
        r = SESSION.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            print(f"❌ OpenMeter lookup failed [{r.status_code}]: {r.text}", flush=True)
            return None
        data = r.json()
        email = data.get("primaryEmail")
        if email:
            print(f"🔎 Found primaryEmail via OpenMeter: {email}", flush=True)
        else:
            print("ℹ️ No primaryEmail in customer record.", flush=True)
        return email
    except Exception as e:
        print("❌ Exception during OpenMeter lookup:", e, flush=True)
        return None

def resolve_recipient_email(payload: dict) -> str:
    """
    Order:
      1) data.subject.email
      2) data.subject.metadata.email
      3) OpenMeter /customers/{subject.key} -> primaryEmail
      4) FALLBACK_TO
    """
    data = payload.get("data", {}) or {}
    subject = data.get("subject", {}) or {}

    # 1) direct email
    direct = subject.get("email")
    if direct:
        print(f"📧 Using subject.email: {direct}", flush=True)
        return direct

    # 2) metadata.email
    meta_email = (subject.get("metadata") or {}).get("email")
    if meta_email:
        print(f"📧 Using subject.metadata.email: {meta_email}", flush=True)
        return meta_email

    # 3) OpenMeter lookup by subject key
    subject_key = subject.get("key")
    if subject_key:
        print(f"🔗 Looking up email for subject.key={subject_key}", flush=True)
        om_email = fetch_email_from_openmeter(subject_key)
        if om_email:
            return om_email

    # 4) fallback
    print(f"⚠️ Falling back to {FALLBACK_TO}", flush=True)
    return FALLBACK_TO

def send_resend_email(to_addr: str, subject: str, body_text: str) -> None:
    resend.Emails.send({
        "from": SENDER,
        "to":   [to_addr],
        "subject": subject,
        "text": body_text,
    })

# ── Webhook ────────────────────────────────────────────────────────
@app.route("/", methods=["POST"])
def handle_openmeter():
    print("==> Webhook hit", flush=True)
    payload = request.get_json(force=True)
    print("🔔 Received:", payload, flush=True)

    etype = payload.get("type")

    # Optional: channel "Send Test"
    if etype == "notification.test":
        to_addr = resolve_recipient_email(payload)
        print(f"🧪 Test event → emailing {to_addr}", flush=True)
        try:
            send_resend_email(
                to_addr,
                "✅ OpenMeter Test Email",
                "This is a test email from OpenMeter."
            )
            print("📨 Test email sent!", flush=True)
        except Exception as e:
            print("❌ Test email failed:", e, flush=True)
        return "", 200

    # Threshold alerts
    if etype == "entitlements.balance.threshold":
        try:
            feature   = payload["data"]["feature"]["name"]
            threshold = payload["data"]["threshold"]["value"]
            to_addr   = resolve_recipient_email(payload)

            print(f"👉 Threshold event: feature={feature}, value={threshold}, to={to_addr}", flush=True)

            send_resend_email(
                to_addr,
                f"⏰ You’ve used {threshold}% of your {feature}",
                (
                    f"Hello,\n\n"
                    f"You’ve now used {threshold}% of your {feature} quota.\n"
                    "Consider upgrading for more minutes.\n\n"
                    "– The Nabrah Team"
                ),
            )
            print(f"✅ Alert email sent to {to_addr}", flush=True)
        except Exception as e:
            print("❌ Failed inside threshold handler:", e, flush=True)
        return "", 200

    print("⚪ Ignored event type:", etype, flush=True)
    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
