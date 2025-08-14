import os
import math
import requests
from flask import Flask, request
from dotenv import load_dotenv
import resend

# ── Setup ──────────────────────────────────────────────────────────────────────
load_dotenv()
resend.api_key       = os.environ["RESEND_API_KEY"]
OPENMETER_API_KEY    = os.environ.get("OPENMETER_API_KEY")
OPENMETER_BASE       = os.environ.get("OPENMETER_BASE", "https://openmeter.cloud")
FALLBACK_TO          = os.environ.get("FALLBACK_TO", "aalguraini@dscan.ai")

SESSION = requests.Session()
if OPENMETER_API_KEY:
    SESSION.headers.update({
        "Authorization": f"Bearer {OPENMETER_API_KEY}",
        "Accept": "application/json",
    })

app = Flask(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────

def extract_subject_key(payload: dict) -> str | None:
    """
    Prefer a real *subject key*.
    We DO NOT use 'subject.id' because that's not a customer key.
    """
    d = payload.get("data", {}) or {}
    # 1) top-level subjectKey if present
    if isinstance(d.get("subjectKey"), str) and d["subjectKey"]:
        return d["subjectKey"]
    # 2) nested subject.key
    subj = d.get("subject") or {}
    key = subj.get("key")
    if isinstance(key, str) and key:
        return key
    return None

def openmeter_get_customer_by_key(customer_key: str) -> dict | None:
    try:
        url = f"{OPENMETER_BASE}/api/v1/customers/{customer_key}"
        r = SESSION.get(url, timeout=10)
        print(f"🔎 OpenMeter GET {url} -> {r.status_code}")
        if r.status_code == 200:
            return r.json()
        else:
            print("⚠️ OpenMeter lookup failed:", r.status_code, r.text)
    except Exception as e:
        print("❌ OpenMeter request error:", e)
    return None

def pick_recipient_email(customer: dict) -> str:
    # prefer metadata.email if present
    meta = (customer or {}).get("metadata") or {}
    meta_email = meta.get("email")
    if isinstance(meta_email, str) and meta_email.strip():
        return meta_email.strip()

    # fallback to primaryEmail
    prim = (customer or {}).get("primaryEmail")
    if isinstance(prim, str) and prim.strip():
        return prim.strip()

    # final fallback
    return FALLBACK_TO

def pick_project_name(customer: dict, payload: dict) -> str:
    # customer.name if we have the customer
    name = (customer or {}).get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()

    # nested subject.displayName from the event
    subj = (payload.get("data") or {}).get("subject") or {}
    disp = subj.get("displayName")
    if isinstance(disp, str) and disp.strip():
        return disp.strip()

    return "Your Project"

def minutes_left_from_payload(payload: dict) -> float | None:
    # OpenMeter sends balance under data.value.balance
    try:
        v = ((payload.get("data") or {}).get("value") or {}).get("balance")
        if v is None:
            return None
        return float(v)
    except Exception:
        return None

def threshold_percent(payload: dict) -> str:
    try:
        val = ((payload.get("data") or {}).get("threshold") or {}).get("value")
        if val is None:
            return "?"
        # make it pretty (no .0 unless needed)
        if float(val).is_integer():
            return f"{int(val)}%"
        return f"{val}%"
    except Exception:
        return "?"

# ── Webhook ────────────────────────────────────────────────────────────────────

@app.route("/", methods=["POST"])
def handle_openmeter():
    payload = request.get_json(force=True)
    print("📬 Received webhook")
    # Only alerts
    if payload.get("type") != "entitlements.balance.threshold":
        print("ℹ️ Ignored event type:", payload.get("type"))
        return "", 200

    # Parse
    s_key = extract_subject_key(payload)
    feature = ((payload.get("data") or {}).get("feature") or {}).get("name") or "Call Minutes"
    meter   = ((payload.get("data") or {}).get("meterSlug")) or "Unknown meter"
    tstr    = threshold_percent(payload)
    mins    = minutes_left_from_payload(payload)

    print(f"🧩 Parsed -> subject_key={s_key}, feature={feature}, meter={meter}, threshold={tstr}")

    customer = None
    if s_key:
        customer = openmeter_get_customer_by_key(s_key)

    # Recipient + project name
    to_email = pick_recipient_email(customer)
    project  = pick_project_name(customer, payload)
    mins_str = f"{mins:.2f} min left" if mins is not None else "minutes left: n/a"

    # Build email
    subj = f"Your Project: You’ve reached {tstr} of your {feature} quota — {mins_str}"
    body = (
        f"Hello,\n\n"
        f"Project: {project}\n"
        f"Call Minutes left: {mins:.2f} min\n" if mins is not None else
        f"Hello,\n\nProject: {project}\n"
    )
    body += (
        f"\nYou’ve reached {tstr} of your {feature} quota for this project.\n"
        f"Consider upgrading for more capacity.\n\n"
        f"— The Nabrah Team"
    )

    params = {
        "from":    "Nabrah <no-reply@nabrah.ai>",
        "to":      [to_email],
        "subject": subj,
        "text":    body,
    }

    try:
        resend.Emails.send(params)
        print(f"✅ Alert email sent to {to_email} | subject=“{subj}”")
    except Exception as e:
        print("❌ Failed to send email:", e)

    return "", 200


if __name__ == "__main__":
    # Render/Heroku style: gunicorn in prod. Local: flask dev server.
    app.run(host="0.0.0.0", port=5000)
