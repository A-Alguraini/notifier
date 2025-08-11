# app.py
import os
import sys
from flask import Flask, request
from dotenv import load_dotenv
import resend
import requests

# ──────────────────────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────────────────────
load_dotenv()

RESEND_API_KEY      = os.getenv("RESEND_API_KEY")
OPENMETER_API_KEY   = os.getenv("OPENMETER_API_KEY")
OPENMETER_BASE_URL  = os.getenv("OPENMETER_BASE_URL", "https://openmeter.cloud/api/v1")
FROM_EMAIL          = os.getenv("FROM_EMAIL", "Nabrah <no-reply@nabrah.ai>")
FALLBACK_EMAIL      = os.getenv("FALLBACK_EMAIL", "aalguraini@dscan.ai")

if not RESEND_API_KEY:
    raise RuntimeError("Missing RESEND_API_KEY")

resend.api_key = RESEND_API_KEY

HTTP_TIMEOUT = 12

SESSION = requests.Session()
if OPENMETER_API_KEY:
    SESSION.headers.update({
        "Authorization": f"Bearer {OPENMETER_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })

app = Flask(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers: email lookup
# ──────────────────────────────────────────────────────────────────────────────

def fetch_email_from_openmeter(subject_key: str) -> str | None:
    """
    Resolve an email for a *subject* key using OpenMeter's API.
    Order:
      1) /customers/{subject_key}            (works if subject key == customer key)
      2) /customers?subjectKey=<subject_key> (recommended, most reliable)
      3) /subjects/{subject_key} -> metadata.email (last resort)
    Returns the email string or None if not found.
    """
    try:
        # 1) Try customers/{key}
        url = f"{OPENMETER_BASE_URL}/customers/{subject_key}"
        r = SESSION.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            email = (r.json() or {}).get("primaryEmail")
            if email:
                print(f"🔎 primaryEmail via customers/{{key}}: {email}", flush=True)
                return email
            print("ℹ️ customers/{key} 200 but no primaryEmail.", flush=True)
        else:
            print(f"ℹ️ customers/{{key}} -> {r.status_code}. Trying filter…", flush=True)

        # 2) Try customers?subjectKey=
        url = f"{OPENMETER_BASE_URL}/customers"
        r = SESSION.get(url, params={"subjectKey": subject_key}, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            data  = r.json() or {}
            items = data.get("items") or data.get("data") or (data if isinstance(data, list) else [])
            if items:
                email = items[0].get("primaryEmail")
                if email:
                    print(f"🔎 primaryEmail via customers?subjectKey=: {email}", flush=True)
                    return email
            print("ℹ️ customers?subjectKey= 200 but no items/primaryEmail.", flush=True)
        else:
            print(f"ℹ️ customers?subjectKey= -> {r.status_code}. Trying subject…", flush=True)

        # 3) Try subjects/{key} metadata.email
        url = f"{OPENMETER_BASE_URL}/subjects/{subject_key}"
        r = SESSION.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            subj = r.json() or {}
            email = (subj.get("metadata") or {}).get("email")
            if email:
                print(f"🔎 email via subjects/{{key}} metadata: {email}", flush=True)
                return email
            print("ℹ️ subjects/{key} 200 but no metadata.email.", flush=True)
        else:
            print(f"ℹ️ subjects/{{key}} -> {r.status_code}.", flush=True)

        return None

    except Exception as e:
        print("❌ OpenMeter lookup error:", e, flush=True)
        return None


def resolve_recipient_email(payload: dict) -> str:
    """Determine recipient email in this order:
       1) subject.email (if present in webhook)
       2) subject.metadata.email (if present)
       3) OpenMeter Customers by subjectKey
       4) fallback email
    """
    data = payload.get("data", {})
    subject = data.get("subject", {}) or {}

    # 1) Direct email in payload
    if subject.get("email"):
        return subject["email"]

    # 2) Metadata email in payload
    meta_email = (subject.get("metadata") or {}).get("email")
    if meta_email:
        return meta_email

    # 3) Lookup by subject key
    subject_key = subject.get("key") or subject.get("id")
    if OPENMETER_API_KEY and subject_key:
        print(f"🔍 Looking up email for subject.key={subject_key}", flush=True)
        found = fetch_email_from_openmeter(subject_key)
        if found:
            return found

    # 4) Fallback
    print(f"📩 Falling back to {FALLBACK_EMAIL}", flush=True)
    return FALLBACK_EMAIL


def build_threshold_subject_and_text(payload: dict) -> tuple[str, str]:
    """Create a friendly subject & text body for threshold alerts."""
    data = payload.get("data", {})
    feature = data.get("feature", {}) or {}
    feature_name = feature.get("displayName") or feature.get("name") or "feature"

    meter_slug = data.get("meterSlug") or feature_name
    threshold  = data.get("threshold", {}) or {}
    t_value    = threshold.get("value")  # e.g., 75, 90, 100

    # Subject
    if isinstance(t_value, (int, float)):
        subject = f"ℹ️ You’ve reached {int(t_value)}% of your {meter_slug} quota"
    else:
        subject = f"ℹ️ Usage update for {meter_slug}"

    # Body
    lines = [
        "Hello,",
        "",
        f"You’ve now used {t_value}% of your {meter_slug} quota." if t_value is not None
            else f"This is an automated usage update for {meter_slug}.",
        "Consider upgrading for more minutes.",
        "",
        "– The Nabrah Team",
    ]
    return subject, "\n".join(lines)

# ──────────────────────────────────────────────────────────────────────────────
# Webhook handler
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/", methods=["POST"])
def handle_openmeter():
    payload = request.get_json(force=True, silent=True) or {}
    print("🔔 Received webhook:", payload, flush=True)

    event_type = payload.get("type")

    # OpenMeter's “Send Test” button
    if event_type == "notification.test":
        print("🧪 Test event received—sending a test email…", flush=True)
        params = {
            "from":    FROM_EMAIL,
            "to":      [FALLBACK_EMAIL],
            "subject": "✅ OpenMeter Test Email",
            "text":    "This is a test email generated by OpenMeter.",
        }
        try:
            resend.Emails.send(params)
            print("📨 Test email sent!", flush=True)
        except Exception as e:
            print("❌ Failed to send test email:", e, flush=True)
        return "", 200

    # Real threshold events (75%, 90%, 100%… any percent)
    if event_type == "entitlements.balance.threshold":
        email_to = resolve_recipient_email(payload)
        subject, text = build_threshold_subject_and_text(payload)

        print(f"👉 Threshold email → to={email_to} | subject={subject}", flush=True)

        params = {
            "from": FROM_EMAIL,
            "to":   [email_to],
            "subject": subject,
            "text":    text,
        }
        try:
            resend.Emails.send(params)
            print(f"✅ Alert email sent to {email_to}", flush=True)
        except Exception as e:
            print("❌ Failed to send alert email:", e, flush=True)

    else:
        print("⚪ Ignored event type:", event_type, flush=True)

    return "", 200

# ──────────────────────────────────────────────────────────────────────────────
# Local run (Render will use gunicorn with $PORT)
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # local dev
    app.run(host="0.0.0.0", port=5000)
