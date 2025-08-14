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

# Optional: dashboard & support links to make the email friendlier
DASHBOARD_URL  = os.getenv("NABRAH_DASHBOARD_URL", "https://app.nabrah.ai")
SUPPORT_EMAIL  = os.getenv("SUPPORT_EMAIL", "support@nabrah.ai")

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


def pick_email(customer: dict | None) -> tuple[str, str]:
    """
    Choose a destination email from the OpenMeter customer record:
      1) customer.primaryEmail
      2) customer.metadata.email
      3) FALLBACK_EMAIL
    Returns (email, source)
    """
    if not customer:
        return FALLBACK_EMAIL, "fallback(no customer)"
    if customer.get("primaryEmail"):
        return customer["primaryEmail"], "primaryEmail"
    meta = customer.get("metadata") or {}
    if isinstance(meta, dict) and meta.get("email"):
        return meta["email"], "metadata.email"
    return FALLBACK_EMAIL, "fallback(no email)"


def project_name_from(customer: dict | None, subject: dict | None) -> str:
    if customer and customer.get("name"):
        return str(customer["name"]).strip()
    if subject and subject.get("displayName"):
        return str(subject["displayName"]).strip()
    if subject and subject.get("id"):
        return str(subject["id"])[:12] + "…"
    return "Your project"


def nice_minutes(val: float | int | None) -> str:
    if val is None:
        return "unknown"
    whole = int(round(val))
    if math.isclose(whole, val, rel_tol=0, abs_tol=0.01):
        unit = "minute" if whole == 1 else "minutes"
        return f"{whole} {unit}"
    return f"{val:.2f} minutes"


# ── Webhook ───────────────────────────────────────────────────────────────────
@app.route("/", methods=["POST"])
def handle_openmeter():
    event = request.get_json(force=True)
    print("📬 Received webhook:", event, flush=True)

    if event.get("type") != "entitlements.balance.threshold":
        print(f"ℹ️  Ignored event type: {event.get('type')}", flush=True)
        return "", 200

    data = event.get("data") or {}
    feature = (data.get("feature") or {}).get("name") or (data.get("feature") or {}).get("key") or "Call Minutes"
    meter   = (data.get("feature") or {}).get("meterSlug") or "call_minutes"
    subject = data.get("subject") or {}

    threshold_val = (data.get("threshold") or {}).get("value")
    balance_left  = (data.get("value") or {}).get("balance")  # minutes remaining
    usage_val     = (data.get("value") or {}).get("usage")    # optional, may be None

    subject_key = subject.get("id") or subject.get("key") or ""
    customer_key = subject_key.replace("-", "") if subject_key else ""

    print(f"🔗 Resolving email via key → subject_key={subject_key} → customer_key={customer_key}", flush=True)
    customer = om_get_customer(customer_key) if customer_key else None

    to_email, src = pick_email(customer)
    proj_name = project_name_from(customer, subject)
    minutes_left_text = nice_minutes(balance_left)

    # ── Friendly content ──────────────────────────────────────────────────────
    percent_text = f"{threshold_val}%"
    subject_line = f"{proj_name}: {percent_text} of {feature} used — {minutes_left_text} left"

    # Plain-text fallback
    text_body = (
        f"Hi,\n\n"
        f"Good to know: your project **{proj_name}** has now used {percent_text} of its {feature}.\n\n"
        f"• Minutes left: {minutes_left_text}\n"
        f"{'• Minutes used: ' + nice_minutes(usage_val) + '\\n' if usage_val is not None else ''}"
        f"\nWhat can you do next?\n"
        f"• Upgrade your plan or top-up minutes\n"
        f"• Keep an eye on usage in your dashboard: {DASHBOARD_URL}\n"
        f"• Need a hand? We’re here to help: {SUPPORT_EMAIL}\n\n"
        f"— The Nabrah Team"
    )

    # HTML version (nicer to read)
    html_body = f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Usage Alert</title>
  </head>
  <body style="margin:0;padding:0;background:#f7f7fb;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
      <tr>
        <td align="center" style="padding:24px;">
          <table width="600" style="max-width:600px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #eee;">
            <tr><td style="padding:24px;">
              <h2 style="margin:0 0 12px 0;color:#111;">Heads up 👋</h2>
              <p style="margin:0 0 20px 0;color:#444;line-height:1.55;">
                <strong>{proj_name}</strong> has used <strong>{percent_text}</strong> of its <strong>{feature}</strong>.
              </p>

              <div style="display:inline-block;padding:10px 14px;border-radius:999px;background:#f0f4ff;color:#1b47ff;margin-bottom:16px;font-weight:600;">
                {minutes_left_text} remaining
              </div>
              {"<div style='color:#666;margin-bottom:16px;'>Used: <strong>" + nice_minutes(usage_val) + "</strong></div>" if usage_val is not None else ""}

              <hr style="border:none;border-top:1px solid #eee;margin:16px 0;" />

              <h3 style="margin:0 0 8px 0;color:#111;">What can I do?</h3>
              <ul style="padding-left:18px;margin:6px 0 16px 0;color:#444;line-height:1.6;">
                <li>Upgrade your plan or top-up minutes</li>
                <li>Keep an eye on usage in your dashboard</li>
                <li>Need help? Reach out any time</li>
              </ul>

              <a href="{DASHBOARD_URL}"
                 style="display:inline-block;background:#1b47ff;color:#fff;text-decoration:none;padding:10px 16px;border-radius:8px;font-weight:600;">
                Open dashboard
              </a>

              <p style="color:#777;margin:20px 0 0 0;font-size:13px;">
                Questions? Email us at <a href="mailto:{SUPPORT_EMAIL}" style="color:#1b47ff;text-decoration:none;">{SUPPORT_EMAIL}</a>.
              </p>

              <p style="color:#999;margin:16px 0 0 0;font-size:12px;">— The Nabrah Team</p>
            </td></tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
    """.strip()

    print(
        f"📣 Threshold fired: feature='{feature}', meter='{meter}', "
        f"threshold={threshold_val}%, minutes_left={balance_left}, usage={usage_val}",
        flush=True
    )
    print(f"📧 Using {src}: {to_email}", flush=True)

    try:
        resend.Emails.send({
            "from": "Nabrah <no-reply@nabrah.ai>",
            "to":   [to_email],
            "subject": subject_line,
            "text":    text_body,
            "html":    html_body,
        })
        print(f"✅ Alert email sent to {to_email} | subject=“{subject_line}”", flush=True)
    except Exception as e:
        print("❌ Failed to send alert email:", e, flush=True)

    return "", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
