import os
import sys
import json
from typing import Optional

from flask import Flask, request, jsonify
import requests
import resend
from dotenv import load_dotenv

# ─── Setup ────────────────────────────────────────────────────────────────────
load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
OPENMETER_API_KEY = os.getenv("OPENMETER_API_KEY")
# Use the REST API endpoint shown in OpenMeter > Integrations > API (same workspace!)
OPENMETER_BASE_URL = os.getenv("OPENMETER_BASE_URL", "https://openmeter.cloud")

if not RESEND_API_KEY:
    raise RuntimeError("Missing RESEND_API_KEY")
if not OPENMETER_API_KEY:
    print("ERROR: Missing OPENMETER_API_KEY (set it in Render env).", flush=True)
    # don't raise; we still want to boot to see 200s to Svix tests, but lookups will fail

resend.api_key = RESEND_API_KEY

FROM_EMAIL = "Nabrah <no-reply@nabrah.ai>"
FALLBACK_TO = os.getenv("FALLBACK_TO", "aalguraini@dscan.ai")

app = Flask(__name__)

# ─── Helpers ──────────────────────────────────────────────────────────────────
def om_get(path: str, params: dict) -> requests.Response:
    """GET to OpenMeter REST API with auth; logs URL and status."""
    url = f"{OPENMETER_BASE_URL.rstrip('/')}{path}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {OPENMETER_API_KEY}" if OPENMETER_API_KEY else "",
    }
    print(f"🌐 OpenMeter GET {url} params={params}", flush=True)
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    print(f"🌐 → {resp.status_code}", flush=True)
    return resp

def extract_email_from_customer(cust: dict) -> Optional[str]:
    # Prefer primaryEmail
    email = cust.get("primaryEmail")
    if email:
        return email
    # Try metadata.email
    meta = cust.get("metadata") or {}
    return meta.get("email")

def lookup_email_for_subject(subject_key: str) -> Optional[str]:
    """Find customer email via subject.key -> customer -> email."""
    if not OPENMETER_API_KEY:
        print("⚠️ No OPENMETER_API_KEY set. Skipping lookup.", flush=True)
        return None

    try:
        # Prefer direct lookup by subjectKey if supported
        # (Many deployments support ?subjectKey=... on /customers)
        resp = om_get("/customers", {"subjectKey": subject_key})
        if resp.status_code == 401:
            print("❌ OpenMeter auth failed (401). Wrong or missing API key.", flush=True)
            return None
        if resp.status_code == 404:
            print("❌ OpenMeter returned 404. Likely wrong workspace/org for this API key.", flush=True)
            return None
        resp.raise_for_status()

        data = resp.json()
        customers = data if isinstance(data, list) else data.get("data", [])
        if not customers:
            print("ℹ️ No customer found for this subject.key.", flush=True)
            return None

        # Use the first match
        cust = customers[0]
        print(f"🧭 Found customer: {cust.get('name') or cust.get('key')}", flush=True)
        email = extract_email_from_customer(cust)
        if email:
            print(f"📧 Resolved recipient email: {email}", flush=True)
            return email
        print("ℹ️ Customer has no primaryEmail/metadata.email.", flush=True)
        return None

    except requests.RequestException as e:
        print(f"❌ OpenMeter request error: {e}", flush=True)
        return None
    except Exception as e:
        print(f"❌ Unexpected lookup error: {e}", flush=True)
        return None

def send_email(to_addr: str, subject: str, text: str):
    """Send email via Resend; logs outcome."""
    try:
        resend.Emails.send({"from": FROM_EMAIL, "to": [to_addr], "subject": subject, "text": text})
        print(f"✅ Email sent → {to_addr} | subject='{subject}'", flush=True)
    except Exception as e:
        print(f"❌ Resend send failed: {e}", flush=True)

# ─── Webhook ──────────────────────────────────────────────────────────────────
@app.route("/", methods=["POST"])
def handle_openmeter():
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        print("❌ Could not parse JSON body.", flush=True)
        return jsonify({"ok": False}), 400

    print("🔔 Received webhook:", json.dumps(payload, indent=2), flush=True)

    event_type = payload.get("type")
    if event_type != "entitlements.balance.threshold":
        print(f"⚪ Ignored event type: {event_type}", flush=True)
        return "", 200

    data = payload.get("data", {})
    feature = (data.get("feature") or {}).get("name")
    meter   = (data.get("meterSlug"))
    subject = (data.get("subject") or {})
    subject_key = subject.get("key")
    threshold_obj = data.get("threshold") or {}
    threshold_val = threshold_obj.get("value")
    threshold_type = threshold_obj.get("type")  # usually 'PERCENT'

    # Log what fired
    print(f"📌 Threshold fired: feature='{feature}', meter='{meter}', "
          f"threshold={threshold_val}{('%' if threshold_type=='PERCENT' else '')}, "
          f"subject.key='{subject_key}'", flush=True)

    # Who should get the email?
    to_addr = lookup_email_for_subject(subject_key) or FALLBACK_TO
    if to_addr == FALLBACK_TO:
        print("🟡 Using fallback email (no customer email found for this subject).", flush=True)

    # Compose message for any percentage
    subj = f"ℹ️ You’ve reached {threshold_val}% of your {meter or feature} quota"
    text = (
        "Hello,\n\n"
        f"You’ve now reached {threshold_val}% of your {meter or feature} quota "
        "based on your current entitlement balance.\n\n"
        "Consider upgrading if you expect more usage.\n\n"
        "– The Nabrah Team"
    )

    # Send it
    print(f"✉️  Sending -> {to_addr}", flush=True)
    send_email(to_addr, subj, text)
    return "", 200

# ─── Health check (optional) ───────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return "ok", 200

if __name__ == "__main__":
    # Local runs
    app.run(host="0.0.0.0", port=5000)
