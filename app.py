import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

OPENMETER_API_KEY = os.getenv("OPENMETER_API_KEY")
OPENMETER_BASE_URL = "https://api.openmeter.io/v1"
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
FALLBACK_EMAIL = "your-fallback@example.com"

# --- Helpers ---
def get_customer_email_from_openmeter(subject_key):
    headers = {"Authorization": f"Bearer {OPENMETER_API_KEY}"}

    # Try direct customer lookup
    resp = requests.get(f"{OPENMETER_BASE_URL}/customers/{subject_key}", headers=headers)
    if resp.status_code == 200:
        email = resp.json().get("primaryEmail")
        if email:
            return email

    # Try filtering by subjectKey
    resp = requests.get(f"{OPENMETER_BASE_URL}/customers", params={"subjectKey": subject_key}, headers=headers)
    if resp.status_code == 200:
        customers = resp.json().get("data", [])
        if customers and customers[0].get("primaryEmail"):
            return customers[0]["primaryEmail"]

    return None


def build_threshold_subject_and_text(feature_name, t_value):
    if t_value == 50:
        subject = f"🟢 50% of your {feature_name} quota used"
    elif t_value == 75:
        subject = f"🟡 75% of your {feature_name} quota used"
    elif t_value == 90:
        subject = f"🟠 90% of your {feature_name} quota used"
    elif t_value == 100:
        subject = f"🔴 100% of your {feature_name} quota used"
    else:
        subject = f"You’ve reached {t_value}% of your {feature_name} quota"

    text = (
        f"Hello,\n\n"
        f"You have now used {t_value}% of your {feature_name} quota.\n"
        f"Consider upgrading for more capacity.\n\n"
        f"- The Nabrah Team"
    )

    html = (
        f"<p>Hello,</p>"
        f"<p>You have now used <b>{t_value}%</b> of your <b>{feature_name}</b> quota.</p>"
        f"<p>Consider upgrading for more capacity.</p>"
        f"<p>- The Nabrah Team</p>"
    )

    return subject, text, html


def send_email_via_resend(to_email, subject, text, html):
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": "Nabrah <no-reply@nabrah.ai>",
        "to": [to_email],
        "subject": subject,
        "text": text,
        "html": html
    }
    resp = requests.post("https://api.resend.com/emails", headers=headers, json=payload)
    resp.raise_for_status()


# --- Webhook endpoint ---
@app.route("/webhook", methods=["POST"])
def webhook_handler():
    data = request.json
    print(f"📩 Received webhook: {data}")

    if data.get("type") != "entitlements.balance.threshold":
        return jsonify({"status": "ignored"})

    subject_key = data["subject"]["key"]
    feature_name = data.get("feature", {}).get("displayName") or data.get("feature", {}).get("name", "Unknown Feature")
    t_value = data.get("threshold", {}).get("value", "Unknown")

    # Look up email from OpenMeter
    email = get_customer_email_from_openmeter(subject_key)
    if not email:
        print(f"⚠️ No customer email found for subject_key={subject_key}, using fallback.")
        email = FALLBACK_EMAIL

    subject, text, html = build_threshold_subject_and_text(feature_name, t_value)

    # Send email
    send_email_via_resend(email, subject, text, html)
    print(f"✅ Alert email sent to {email}")
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(port=5000)
