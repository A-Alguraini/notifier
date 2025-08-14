# app.py (only the changes below matter)

import os, sys, requests
from flask import Flask, request
import resend
from dotenv import load_dotenv

load_dotenv()
resend.api_key = os.getenv("RESEND_API_KEY")
OPENMETER_API_KEY = os.getenv("OPENMETER_API_KEY")
OPENMETER_BASE = os.getenv("OPENMETER_BASE", "https://openmeter.cloud/api/v1")

FALLBACK_EMAIL = "aalguraini@dscan.ai"   # your current fallback

app = Flask(__name__)

def _om_headers():
    return {
        "Authorization": f"Bearer {OPENMETER_API_KEY}",
        "Accept": "application/json",
    }

def get_customer_from_openmeter(subject_key: str):
    """
    Resolve a customer from a subject_key.
    1) Try `/customers/{customer_key}` where customer_key is subject_key with hyphens removed.
    2) If 404, try `/customers?subjectKey={subject_key}` and use the first hit.
    Return (customer_dict or None).
    """
    # 1) direct by key (hyphens removed)
    customer_key = subject_key.replace("-", "")
    url = f"{OPENMETER_BASE}/customers/{customer_key}"
    r = requests.get(url, headers=_om_headers(), timeout=10)
    print(f"🔎 OpenMeter GET {url} -> {r.status_code}", flush=True)

    if r.status_code == 200:
        return r.json()

    if r.status_code != 404:
        print(f"⚠️ Unexpected status {r.status_code}: {r.text}", flush=True)

    # 2) fallback: query by subjectKey (some orgs allow this)
    url2 = f"{OPENMETER_BASE}/customers"
    r2 = requests.get(url2, headers=_om_headers(), params={"subjectKey": subject_key}, timeout=10)
    print(f"🔎 OpenMeter GET {url2}?subjectKey={subject_key} -> {r2.status_code}", flush=True)
    if r2.status_code == 200:
        body = r2.json()
        # API returns either a single object or a list; handle both
        if isinstance(body, dict) and body.get("key"):
            return body
        if isinstance(body, list) and body:
            return body[0]

    return None

def send_email(to_addr: str, subject: str, text: str):
    try:
        resend.Emails.send({
            "from": "Nabrah <no-reply@nabrah.ai>",
            "to": [to_addr],
            "subject": subject,
            "text": text,
        })
        print(f"✅ Alert email sent to {to_addr} | subject=“{subject}”", flush=True)
    except Exception as e:
        print("❌ Failed to send email:", e, flush=True)

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    print("📥 Received webhook:", data, flush=True)

    if data.get("type") != "entitlements.balance.threshold":
        print("ℹ️ Ignored event type:", data.get("type"), flush=True)
        return "", 200

    # Extract details
    subj = data["data"]["subject"]
    subject_key = subj.get("key") or subj.get("id")
    feature = data["data"]["feature"]["name"]
    threshold = data["data"]["threshold"]["value"]
    value = data["data"]["value"]         # this contains balance/usage/overage
    balance = float(value.get("balance", 0.0))

    print(f"🧩 Parsed -> subject_key={subject_key}, feature={feature}, threshold={threshold}%", flush=True)

    # Resolve customer
    customer = get_customer_from_openmeter(subject_key) if subject_key else None

    # Recipient and project name
    to_addr = FALLBACK_EMAIL
    project_name = "Your Project"

    if customer:
        # prefer primaryEmail; fallback to metadata.email
        to_addr = customer.get("primaryEmail") or (customer.get("metadata") or {}).get("email") or FALLBACK_EMAIL
        # prefer customer name; fallback to subject displayName
        project_name = customer.get("name") or (subj.get("displayName") or "Your Project")
        print(f"📧 Resolved customer: name='{project_name}', email='{to_addr}'", flush=True)
    else:
        print("⚠️ No customer found (using fallback email & generic project name)", flush=True)

    subject = f"📈 {project_name}: You’ve reached {threshold}% of your Call Minutes quota — {balance:.2f} min left"
    body = (
        f"Hello,\n\n"
        f"Project: {project_name}\n"
        f"Call Minutes left: {balance:.2f} min\n\n"
        f"You’ve reached {threshold}% of your Call Minutes quota for this project.\n"
        f"Consider upgrading for more capacity.\n\n"
        f"— The Nabrah Team"
    )
    send_email(to_addr, subject, body)
    return "", 200


# gunicorn entrypoint expects `app` variable
