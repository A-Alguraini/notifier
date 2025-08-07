import os
import resend
from dotenv import load_dotenv
from flask import Flask, request

# — your existing setup —
load_dotenv()
resend.api_key = os.environ["RESEND_API_KEY"]
base_params = {
  "from":    "Nabrah <no-reply@nabrah.ai>",
  "to":      ["aalguraini@dscan.ai"],
  "subject": "Test Email",
  "text":    "This is a test email message",
}

# — new doorbell code —
app = Flask(__name__)

@app.route("/", methods=["POST"])
def on_openmeter_alert():
  data = request.get_json(force=True)
  print("🔔 Got alert:", data)

  # Only care about 90%‐of‐minutes alerts
  if data.get("type") == "entitlements.balance.threshold":
    feature   = data["data"]["feature"]["name"]
    threshold = data["data"]["threshold"]["value"]
    if feature == "subscription_minutes" and threshold == 90:
      # build & send your email
      params = base_params.copy()
      params["subject"] = f"⏰ {threshold}% of your {feature} used!"
      resend.Emails.send(params)
      print("✅ Sent email!")

  return "", 200

if __name__ == "__main__":
  # this makes it listen on port 5000
  app.run(host="0.0.0.0", port=5000)



import sys

@app.route("/", methods=["POST"])
def on_alert():
    data = request.get_json(force=True)

    # Print + flush so you see it immediately
    print("🔔 Alert received:", data, flush=True)
    sys.stdout.flush()

    if data.get("type") == "entitlements.balance.threshold":
        feat      = data["data"]["feature"]["name"]
        thresh    = data["data"]["threshold"]["value"]
        if feat == "subscription_minutes" and thresh == 90:
            print("👉 90% threshold hit – sending email…", flush=True)
            sys.stdout.flush()

            # your email send code…
            resend.Emails.send(params)

            print("✅ Email sent!", flush=True)
            sys.stdout.flush()

    return "", 200
