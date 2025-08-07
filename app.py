import os
import sys
from flask import Flask, request
import resend
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("RESEND_API_KEY")
if not api_key:
    raise RuntimeError("Missing RESEND_API_KEY in .env")
resend.api_key = api_key

app = Flask(__name__)

@app.route("/", methods=["POST"])
def handle_openmeter():
    data = request.get_json(force=True)
    print("🔔 Received webhook:", data, flush=True)

    if data.get("type") != "entitlements.balance.threshold":
        print("⚪ Ignored event type:", data.get("type"), flush=True)
        return "", 200

    feature   = data["data"]["feature"]["name"]
    threshold = data["data"]["threshold"]["value"]

    if feature == "subscription_minutes" and threshold == 90:
        print("👉 90% threshold hit for feature", feature, flush=True)

        email_to = data["data"]["subject"].get("email", "aalguraini@dscan.ai")
        params = {
            "from":    "Nabrah <no-reply@nabrah.ai>",
            "to":      [email_to],
            "subject": f"⏰ {threshold}% of your {feature} used!",
            "text":    f"Hi there,\n\nYou’ve now used {threshold}% of your {feature} quota.\n\n– The Nabrah Team"
        }
        try:
            resend.Emails.send(params)
            print(f"✅ Email sent to {email_to}", flush=True)
        except Exception as e:
            print("❌ Failed to send email:", e, flush=True)
    else:
        print(f"⚪ Not our rule: feature={feature} threshold={threshold}", flush=True)

    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
