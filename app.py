import os
import sys
from flask import Flask, request
import resend
from dotenv import load_dotenv

# ─── Setup ───────────────────────────────────────
load_dotenv()
api_key = os.getenv("RESEND_API_KEY")
if not api_key:
    raise RuntimeError("Missing RESEND_API_KEY in .env")
resend.api_key = api_key

base_params = {
    "from":    "Nabrah <no-reply@nabrah.ai>",
    "to":      ["abdulaziz.alguraini@gmail.com"],  # Try changing this to a gmail/yahoo/hotmail for testing!
}

app = Flask(__name__)

@app.route("/", methods=["POST"])
def handle_openmeter():
    print("==> Flask route called", flush=True)
    try:
        data = request.get_json(force=True)
        print("🔔 Received webhook:", data, flush=True)
    except Exception as e:
        print("❌ Failed to get JSON:", e, flush=True)
        return "", 400

    try:
        event_type = data.get("type")
        print("Event type:", event_type, flush=True)
        if event_type == "entitlements.balance.threshold":
            print("IN THRESHOLD HANDLER", flush=True)
            try:
                feature   = data["data"]["feature"]["name"]
                threshold = data["data"]["threshold"]["value"]
                print(f"Got threshold event: feature={feature}, value={threshold}", flush=True)

                alert_params = {
                    "from":    base_params["from"],
                    "to":      base_params["to"],
                    "subject": f"⏰ You’ve used {threshold}% of your {feature}",
                    "text":    (
                        f"Hello,\n\n"
                        f"You’ve now used {threshold}% of your {feature} quota.\n"
                        "Consider upgrading for more minutes.\n\n"
                        "– The Nabrah Team"
                    )
                }
                print(f"Sending alert email to {base_params['to'][0]}...", flush=True)
                resend.Emails.send(alert_params)
                print(f"✅ Alert email sent to {base_params['to'][0]}", flush=True)
            except Exception as e:
                print("❌ Failed inside threshold block:", e, flush=True)
        else:
            print("⚪ Ignored event type:", event_type, flush=True)
    except Exception as e:
        print("❌ General failure in handler:", e, flush=True)
    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
