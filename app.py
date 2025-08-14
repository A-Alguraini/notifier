import os
import sys
import requests
from flask import Flask, request
import resend
from dotenv import load_dotenv

# ─── Setup ─────────────────────────────────────────────────────────────────────
load_dotenv()

# Resend
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
if not RESEND_API_KEY:
    raise RuntimeError("Missing RESEND_API_KEY")
resend.api_key = RESEND_API_KEY

FROM_EMAIL = os.getenv("FROM_EMAIL", "Nabrah <no-reply@nabrah.ai>")
FALLBACK_EMAIL = os.getenv("FALLBACK_EMAIL", "aalguraini@dscan.ai")  # keep your own default

# OpenMeter
OM_API_KEY = os.getenv("OPENMETER_API_KEY")
if not OM_API_KEY:
    raise RuntimeError("Missing OPENMETER_API_KEY")
OM_BASE = os.getenv("OPENMETER_BASE", "https://openmeter.cloud")

app = Flask(__name__)

# ─── Helpers ───────────────────────────────────────────────────────────────────
def get_customer_email_from_subject(subject_key: str) -> str | None:
    """
    subject_key looks like: '01989852-e5b1-7b7d-aac4-cf09fd93fade'
    customer_key is the same but without dashes.
    """
    if not subject_key:
        return None

    customer_key = subject_key.replace("-", "")
    url = f"{OM_BASE}/api/v1/customers/{customer_key}"
    headers = {
        "Authorization": f"Bearer {OM_API_KEY}",
        "Accept": "application/json",
    }

    try:
        r = requests.get(url, headers=headers, timeout=10)
        print(f"🔎 OpenMeter GET {url} -> {r.status_code}", flush=True)
        if r.status_code == 200:
            j = r.json()
            # Prefer primaryEmail, then metadata.email
            email = j.get("primaryEmail")
            if not email:
                meta = j.get("metadata") or {}
                email = meta.get("email")
            return email
        elif r.status_code == 404:
            print("ℹ️ No customer found for this subject (404).", flush=True)
        else:
            print(f"⚠️ OpenMeter error: {r.status_code} {r.text}", flush=True)
    except Exception as e:
        print(f"❌ OpenMeter request failed: {e}", flush=True)

    return None


def safe_get(d: dict, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur if cur is not None else default


# ─── Webhook ───────────────────────────────────────────────────────────────────
@app.route("/", methods=["POST"])
def handle_openmeter():
    data = request.get_json(force=True)
    print("📥 Received webhook:", data, flush=True)

    event_type = data.get("type")

    # We only react to balance thresholds (your rules fire these)
    if event_type != "entitlements.balance.threshold":
        print(f"⚪ Ignored event type: {event_type}", flush=True)
        return "", 200

    # Pull fields we care about
    subject_key = safe_get(data, "data", "subject", "key")
    meter_name  = safe_get(data, "data", "feature", "name", default="Your meter")
    meter_slug  = safe_get(data, "data", "meterSlug", default=None)
    threshold_v = safe_get(data, "data", "threshold", "value", default=None)
    threshold_t = safe_get(data, "data", "threshold", "type",  default="PERCENT")

    # If meterSlug is present use that (short name), else use feature.name (nice name)
    meter_label = meter_slug or meter_name or "meter"

    # Threshold percent (formatted)
    if threshold_t == "PERCENT" and isinstance(threshold_v, (int, float)):
        threshold_str = f"{int(threshold_v)}%"
    else:
        threshold_str = str(threshold_v) if threshold_v is not None else "?"

    print(
        f"🔔 Threshold fired: feature='{meter_name}', meter='{meter_label}', "
        f"threshold={threshold_str}, subject_key='{subject_key}'",
        flush=True,
    )

    # Look up recipient by *customer key* derived from subject key (remove dashes)
    to_email = get_customer_email_from_subject(subject_key)
    if not to_email:
        to_email = FALLBACK_EMAIL
        print(f"📩 Using fallback email {to_email} (no customer email found).", flush=True)

    # Build the email
    subject = f"📈 You’ve reached {threshold_str} of your {meter_label} quota"
    text = (
        f"Hello,\n\n"
        f"You’ve reached {threshold_str} of your {meter_label} quota.\n"
        f"Consider upgrading for more capacity.\n\n"
        f"– The Nabrah Team"
    )

    try:
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to":   [to_email],
            "subject": subject,
            "text":    text,
        })
        print(f"✅ Alert email sent to {to_email} | subject=“{subject}”", flush=True)
    except Exception as e:
        print(f"❌ Failed to send alert email: {e}", flush=True)

    return "", 200


if __name__ == "__main__":
    # Local run
    app.run(host="0.0.0.0", port=5000)
