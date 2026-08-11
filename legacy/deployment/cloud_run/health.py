"""Health check server for Cloud Run / Kubernetes liveness probes."""

import os
import json
from http.server import HTTPServer, BaseHTTPRequestHandler


class HealthHandler(BaseHTTPRequestHandler):
    """Responds with 200 OK for health checks.

    Checks:
    1. Python runtime is alive (inherent)
    2. Environment variables are set
    3. Data directory is writable
    """

    def do_GET(self):
        if self.path == "/health":
            status = {"status": "healthy", "service": "renovai"}

            # Check critical env vars
            missing_vars = []
            for var in ["GOOGLE_API_KEY", "RENOVAI_IDENTITY_SECRET"]:
                if not os.getenv(var):
                    missing_vars.append(var)

            if missing_vars:
                status["status"] = "degraded"
                status["missing_env_vars"] = missing_vars

            # Check data directory
            data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
            if not os.access(data_dir, os.W_OK):
                status["status"] = "degraded"
                status["data_dir_writable"] = False

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(status).encode())
        else:
            self.send_response(404)
            self.end_headers()


def main():
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
