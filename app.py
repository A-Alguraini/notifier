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

OPENMETER_BASE_URL = "https://openmeter.cloud/api/v1"     # Cloud base URL
FALLBACK_TO = "aalguraini@dscan.ai"                       # safety net

# HTTP
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

# ---------- Email content builders ----------

def _fmt(n):
    try:
        n = float(n)
        if n.is_integer():
            return f"{int(n):,}"
        return f"{n:,.2f}"
    except Exception:
        return str(n)

def _as_int_percent(v):
    """Turn 100, '100', '100%', ' 100 % ' → 100; return None if unknown."""
    try:
        s = str(v).strip().replace("%", "")
        return int(float(s))
    except Exception:
        return None

def build_threshold_email(payload: dict) -> tuple[str, str]:
    """
    Build (subject, body) for any entitlements.balance.threshold.
    - Robust percent parsing
    - If balance==0 or hasAccess==false -> treat as 100%
    - Custom text for 75/90/100, sensible default otherwise
    """
    d = payload["data"]
    feature = d["feature"]["name"]

    th       = d.get("threshold", {}) or {}
    th_type  = th.get("type", "PERCENT")
    th_value = th.get("value")

    values     = d.get("value", {}) or {}
    used       = values.get("usage")
    balance    = values.get("balance")
    has_access = values.get("hasAccess")

    # Parse percent cleanly
    pct = _as_int_percent(th_value) if th_type == "PERCENT" else None

    # Treat out-of-balance as 100%
    exhausted = (has_access is False) or (balance == 0)
    if exhausted:
        pct = 100

    # Subject line
    if pct == 75:
        subject = f"🔔 You’ve used 75% of your {feature}"
    elif pct == 90:
        subject = f"⏰ You’ve used 90% of your {feature}"
    elif pct == 100:
        subject = f"⛔ You’ve used 100% of your {feature} (no balance left)"
    else:
        subject = (
            f"ℹ️ You’ve reached {th_value}% of your {feature}"
            if th_type == "PERCENT" and th_value is not None
            else f"ℹ️ {feature} threshold reached"
        )

    # Body
    parts = ["Hello,", ""]
    if pct is not None:
        parts.append(f"You’ve now used {pct}% of your {feature} quota.")
    else:
        parts.append(f"You’ve reached the configured {feature} threshold.")

    extras = []
    if used is not None:
        extras.append(f"Used: {_fmt(used)}")
    if balance is not None:
        extras.append(f"Remaining: {_fmt(balance)}")
    if extras:
        parts.append(" • " + " | ".join(extras))

    if pct == 100 or exhausted:
        parts += ["", "Your balance is depleted. Upgrade or top up to continue."]
    elif pct is not None and pct >= 90:
        parts += ["", "You’re almost out. Consider upgrading your plan so there’s no interruption."]
    else:
        parts += ["", "Heads up! You’re getting close. Upgrade any time if you need more."]

    parts += ["", "– The Nabrah Team"]
    return subject, "\n".join(parts)

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

    # Threshold alerts (all rules)
    if etype == "entitlements.balance.threshold":
        try:
            to_addr = resolve_recipient_email(payload)
            subject, body = build_threshold_email(payload)
            print(f"👉 Threshold email → to={to_addr} | subject={subject}", flush=True)
            send_resend_email(to_addr, subject, body)
            print(f"✅ Alert email sent to {to_addr}", flush=True)
        except Exception as e:
            print("❌ Failed inside threshold handler:", e, flush=True)
        return "", 200

    print("⚪ Ignored event type:", etype, flush=True)
    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
