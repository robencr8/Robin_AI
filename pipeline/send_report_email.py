#!/usr/bin/env python3.11
import json, subprocess

with open("/home/ubuntu/automation/report_20260424.html") as f:
    html_body = f.read()

payload = json.dumps({
    "messages": [{
        "to": ["robenedwan@gmail.com"],
        "subject": "ECO Technology — Daily Intelligence Report v2.0 | Friday, 24 April 2026",
        "content": html_body,
        "mimeType": "text/html"
    }]
})

result = subprocess.run(
    ["manus-mcp-cli", "tool", "call", "gmail_send_messages", "--server", "gmail", "--input", payload],
    capture_output=True, text=True, timeout=60
)
print(result.stdout)
print(result.stderr)
