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

OPENMETER_API_URL = os.getenv("OPENMETER_API_URL", "https://openmeter.cloud/api/v1")
FALLBACK_TO = os.getenv("FALLBACK_TO", "aalguraini@dscan.ai")
BASE_FROM = "Nabrah <no-reply@nabrah.ai>"

resend.api_key = RESEND_API_KEY

HTTP_HEADERS = {
    "Authorization": f"Bearer {OPENMETER_API_KEY}",
    "Content-Type": "application/json",
}

app = Flask(__name__)

# ─── Helpers ───────────────────────────────────────────────────────────────────
def dashless(s: str) -> str:
    return s.replace("-", "") if s else s

def _get(url: str, **kwargs) -> requests.Response:
    return requests.get(url, headers=HTTP_HEADERS, timeout=10, **kwargs)

def find_customer_email(subject_key: str) -> str | None:
    """
    Resolve the recipient email.
    Strategy:
      1) /customers?subjectKey=<subject_key>
      2) /customers/<dashless(subject_key)>
      3) /customers?key=<dashless(subject_key)>
      4) None -> caller will use fallback
    """
    if not subject_key:
        print("⚠️ No subject_key provided to find_customer_email", flush=True)
        return None

    # 1) Directly by subjectKey (best if relationship is set in OpenMeter)
    try:
        url = f"{OPENMETER_API_URL}/customers"
        r = _get(url, params={"subjectKey": subject_key})
        print(f"🔎 GET {url}?subjectKey={subject_key} -> {r.status_code}", flush=True)
        if r.status_code == 200:
            items = (r.json() or {}).get("items", [])
            if items:
                cust = items[0]
                email = cust.get("primaryEmail") or (cust.get("metadata") or {}).get("email")
                if email:
                    print(f"✅ email={email} via subjectKey", flush=True)
                    return email
    except Exception as e:
        print(f"⚠️ subjectKey lookup error: {e}", flush=True)

    # 2) By customer key (dashless subject_key)
    dk = dashless(subject_key)
    try:
        url = f"{OPENMETER_API_URL}/customers/{dk}"
        r = _get(url)
        print(f"🔎 GET {url} -> {r.status_code}", flush=True)
        if r.status_code == 200:
            cust = r.json() or {}
            email = cust.get("primaryEmail") or (cust.get("metadata") or {}).get("email")
            if email:
                print(f"✅ email={email} via /customers/{{key}}", flush=True)
                return email
    except Exception as e:
        print(f"⚠️ /customers/{{key}} lookup error: {e}", flush=True)

    # 3) By ?key=<dashless(subject_key)>
    try:
        url = f"{OPENMETER_API_URL}/customers"
        r = _get(url, params={"key": dk})
        print(f"🔎 GET {url}?key={dk} -> {r.status_code}", flush=True)
        if r.status_code == 200:
            items = (r.json() or {}).get("items", [])
            if items:
                cust = items[0]
                email = cust.get("primaryEmail") or (cust.get("metadata") or {}).get("email")
                if email:
                    print(f"✅ email={email} via key query", flush=True)
                    return email
    except Exception as e:
        print(f"⚠️ ?key lookup error: {e}", flush=True)

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
    body = request.get_json(force=True)
    print("📥 Received webhook:", body, flush=True)

    event_type = body.get("type")
    payload = body.get("data", {}) or {}   # <— REAL fields are inside data

    if event_type == "notification.test":
        print(f"🧪 Test event → emailing {FALLBACK_TO}", flush=True)
        send_email(FALLBACK_TO, "✅ OpenMeter Test Email", "This is a test email from OpenMeter.")
        return "", 200

    if event_type == "entitlements.balance.threshold":
        # Extract robustly from payload
        subject_obj = payload.get("subject", {}) or {}
        subject_key = subject_obj.get("key") or subject_obj.get("id")  # either is fine

        feature_name = (payload.get("feature") or {}).get("name") or "Unknown Feature"
        meter_slug   = payload.get("meterSlug") or (payload.get("meter") or {}).get("slug") or "Unknown meter"
        threshold    = (payload.get("threshold") or {}).get("value")

        print(f"🔧 Parsed → subject_key={subject_key}, feature={feature_name}, meter={meter_slug}, threshold={threshold}", flush=True)

        # Find recipient email
        to_email = find_customer_email(subject_key) or FALLBACK_TO
        if to_email == FALLBACK_TO:
            print(f"↩️ Using fallback email {FALLBACK_TO}", flush=True)

        # Nice subject/body
        t_display = f"{threshold}%" if threshold is not None else "some"
        subject_line = f"📊 You’ve reached {t_display} of your {meter_slug} quota"
        body_text = (
            f"Hello,\n\n"
            f"Your usage for “{feature_name}” ({meter_slug}) has reached {t_display}.\n"
            f"If you need more capacity, please consider upgrading.\n\n"
            f"– The Nabrah Team"
        )

        print(f"✉️  Sending → to={to_email} | subject={subject_line}", flush=True)
        try:
            send_email(to_email, subject_line, body_text)
            print("✅ Alert email sent.", flush=True)
        except Exception as e:
            print("❌ Failed to send alert email:", e, flush=True)

        return "", 200

    print("ℹ️ Ignored event type:", event_type, flush=True)
    return "", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
