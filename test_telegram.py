
import os
import json
import urllib.parse
import urllib.request

token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]

message = (
    "🚗 USA Auto Finder v3.0\n\n"
    "✅ Telegram pranesimu testas sekmingas!\n"
    "🔎 Copart ir IAA paieskos sistema veikia."
)

data = urllib.parse.urlencode({
    "chat_id": chat_id,
    "text": message
}).encode("utf-8")

url = f"https://api.telegram.org/bot{token}/sendMessage"
request = urllib.request.Request(url, data=data)

with urllib.request.urlopen(request, timeout=30) as response:
    result = json.load(response)

if not result.get("ok"):
    raise RuntimeError("Telegram testas nepavyko")

print("Telegram testinis pranesimas issiustas!")
