import os
import resend
from dotenv import load_dotenv

load_dotenv()
print("Loaded API key:", os.environ["RESEND_API_KEY"])
resend.api_key = os.environ["RESEND_API_KEY"]

params = {
    "from":    "Nabrah <no-reply@nabrah.ai>",
    "to":      ["aalguraini@dscan.ai"],
    "subject": "Test Email",
    "text":    "This is a test email message",
}
resend.Emails.send(params)
