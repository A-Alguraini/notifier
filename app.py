import os
import sys
import requests
from flask import Flask, request
import resend
from dotenv import load_dotenv

# ─── Config ────────────────────────────────────────────────────────────────────
load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
if not RESEND_API_KEY:
    raise RuntimeError("Missing RESEND_API_KEY")

OPENMETER_API_KEY = os.getenv("OPENMETER_API_KEY")
if not OPENMETER_API_KEY:
    raise RuntimeError("Missing OPENMETER_API_KEY")

# default OM URL; change if you’re on a different base
OPENMETER_API_URL = os.getenv("OPENMETER_API_URL", "https://openmeter.cloud/api/v1")

resend.api_key = RESEND_API_KEY

# the address to use ONLY if we can’t resolve a recipient
FALLBACK_TO = os.getenv("FALLBACK_TO", "aalguraini@dscan.ai")

BASE_FROM = "Nabrah <no-reply@nabrah.ai>"

HTTP_HEADERS = {
    "Authorization": f"Bearer {OPENMETER_API_KEY}",
    "Content-Type": "application/json",
}

app = Flask(__name__)


# ─── Helpers ───────────────────────────────────────────────────────────────────
def dashless(s: str) -> str:
    """Remove hyphens from a key."""
    return s.replace("-", "") if s else s


def get_customer_by_key(customer_key: str) -> dict | None:
    """
    Try to fetch a single customer by key (dashless) from OpenMeter.
    We try /customers/{key} first; if 404, fall back to /customers?key=...
    """
    if not customer_key:
        return None

    # 1) Direct path
    url_direct = f"{OPENMETER_API_URL}/customers/{customer_key}"
    try:
        r = requests.get(url_direct, headers=HTTP_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
        elif r.status_code != 404:
            print(f"⚠️ OpenMeter GET {url_direct} -> {r.status_code} {r.text}", flush=True)
    except Exception as e:
        print(f"⚠️ Error calling {url_direct}: {e}", flush=True)

    # 2) Query fallback
    url_query = f"{OPENMETER_API_URL}/customers"
    try:
        r = requests.get(url_query, params={"key": customer_key}, headers=HTTP_HEADERS, timeout=10)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                return items[0]
        else:
            print(f"⚠️ OpenMeter GET {url_query}?key={customer_key} -> {r.status_code} {r.text}", flush=True)
    except Exception as e:
        print(f"⚠️ Error calling {url_query}: {e}", flush=True)

    return None


def resolve_email_from_subject_key(subject_key: str) -> str | None:
    """
    Your convention: customer.key == subject.key but without dashes.
    Use that to fetch the customer and return primaryEmail.
    """
    customer_key = dashless(subject_key)
    print(f"🔎 Resolving email via dashless key: subject_key={subject_key} -> customer_key={customer_key}", flush=True)

    cust = get_customer_by_key(customer_key)
    if cust:
        email = cust.get("primaryEmail")
        if email:
            print(f"✅ Found customer email: {email}", flush=True)
            return email
        else:
            print("⚠️ Customer found but no primaryEmail field.", flush=True)
    else:
        print("⚠️ No customer found for constructed key.", flush=True)

    return None


def send_email(to_email: str, subject: str, text: str):
    params = {
        "from": BASE_FROM,
        "to": [to_email],
        "subject": subject,
        "text": text,
    }
    resend.Emails.send(params)


# ─── Webhook ───────────────────────────────────────────────────────────────────
@app.route("/", methods=["POST"])
def handle_openmeter():
    data = request.get_json(force=True)
    print("📥 Received webhook:", data, flush=True)

    event_type = data.get("type")

    if event_type == "notification.test":
        # If you click "Send test" in OpenMeter
        to_email = FALLBACK_TO
        print(f"🧪 Test event → emailing {to_email}", flush=True)
        send_email(
            to_email,
            "✅ OpenMeter Test Email",
            "This is a test email from OpenMeter.",
        )
        return "", 200

    if event_type == "entitlements.balance.threshold":
        subj = data.get("subject", {}) or {}
        subject_key = subj.get("key")  # example: "01989826-2b95-7ccb-8582-c8137c539a5c"

        feat   = (data.get("feature") or {}).get("name", "Unknown Feature")
        thresh = (data.get("threshold") or {}).get("value", "N/A")
        meter  = (data.get("meterSlug") or data.get("meter", {}).get("slug") or "Unknown meter")

        # NEW: derive customer key from subject.key (remove dashes), then fetch email
        to_email = resolve_email_from_subject_key(subject_key) or FALLBACK_TO
        if to_email == FALLBACK_TO:
            print(f"↩️ Using fallback email {FALLBACK_TO} (subject_key={subject_key})", flush=True)

        subject_line = f"📊 You’ve reached {thresh}% of your {meter} quota"
        body = (
            f"Hello,\n\n"
            f"Your usage for “{feat}” ({meter}) has reached {thresh}%.\n"
            f"If you need more capacity, please consider upgrading.\n\n"
            f"– The Nabrah Team"
        )

        print(f"✉️  Sending alert email → to={to_email} | subject={subject_line}", flush=True)
        try:
            send_email(to_email, subject_line, body)
            print("✅ Alert email sent.", flush=True)
        except Exception as e:
            print("❌ Failed to send alert email:", e, flush=True)

        return "", 200

    # Unknown / ignored events
    print("ℹ️ Ignored event type:", event_type, flush=True)
    return "", 200


# ─── Local run ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Render/Gunicorn will import app:app, but local dev can run this:
    app.run(host="0.0.0.0", port=5000)
