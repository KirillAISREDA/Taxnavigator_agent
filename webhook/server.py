#!/usr/bin/env python3
"""
GitHub Webhook Receiver for auto-deploy.
Runs as a separate lightweight service on the server.
Listens for push events and triggers deploy.sh.
"""

import hashlib
import hmac
import json
import os
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# ============================================
# CONFIGURATION
# ============================================
WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "change-me-to-a-random-string")
DEPLOY_SCRIPT = os.environ.get("DEPLOY_SCRIPT", "/opt/Taxnavigator_agent/deploy.sh")
DEPLOY_BRANCH = os.environ.get("DEPLOY_BRANCH", "main")
LISTEN_PORT = int(os.environ.get("WEBHOOK_PORT", "9000"))
LOG_FILE = os.environ.get("DEPLOY_LOG", "/opt/Taxnavigator_agent/deploy.log")


def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify GitHub webhook signature (HMAC-SHA256)."""
    if not signature:
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


class WebhookHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        # Only accept /webhook path
        if self.path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return

        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(content_length)

        # Verify signature
        signature = self.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(payload, signature):
            log("❌ Invalid webhook signature — rejected")
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Invalid signature")
            return

        # Parse event
        event = self.headers.get("X-GitHub-Event", "")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        # Only deploy on push to the target branch
        if event == "push":
            ref = data.get("ref", "")
            branch = ref.replace("refs/heads/", "")

            if branch == DEPLOY_BRANCH:
                pusher = data.get("pusher", {}).get("name", "unknown")
                commit = data.get("head_commit", {}).get("message", "")[:80]
                log(f"🚀 Push to {branch} by {pusher}: {commit}")
                log(f"   Running {DEPLOY_SCRIPT}...")

                # Run deploy asynchronously (don't block the webhook response)
                try:
                    subprocess.Popen(
                        ["/bin/bash", DEPLOY_SCRIPT],
                        stdout=open(LOG_FILE, "a"),
                        stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(DEPLOY_SCRIPT),
                    )
                    log("   Deploy script started ✅")
                except Exception as e:
                    log(f"   ❌ Failed to start deploy: {e}")

                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Deploy triggered")
            else:
                log(f"ℹ️  Push to {branch} — skipping (only {DEPLOY_BRANCH} triggers deploy)")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(f"Skipped: branch {branch}".encode())
        elif event == "ping":
            log("🏓 Ping received from GitHub — webhook configured correctly!")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"pong")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(f"Ignored event: {event}".encode())

    def do_GET(self):
        """Simple health check."""
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Webhook listener OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default HTTP logging — we use our own."""
        pass


def main():
    log(f"🎯 Webhook listener starting on port {LISTEN_PORT}")
    log(f"   Deploy branch: {DEPLOY_BRANCH}")
    log(f"   Deploy script: {DEPLOY_SCRIPT}")

    server = HTTPServer(("0.0.0.0", LISTEN_PORT), WebhookHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Webhook listener stopped")
        server.server_close()


if __name__ == "__main__":
    main()
