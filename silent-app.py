from io import BytesIO
import os

import pathvalidate
from flask import render_template, request, send_file

from app import (WG_CONFIG_NAME, app, ensure_endpoint_ip,
                 ensure_user, generate_client_wireguard_config)

INTRA_URL = os.getenv("INTRA_URL", "https://asdf")
INTRA_PORT = int(os.getenv("INTRA_PORT", "5001"))


@app.route("/silent/download-config")
def silent_download_config():
    ip_address = request.headers.get('X-Real-IP')
    user = ensure_user(ip_address)
    filename = pathvalidate.sanitize_filename(f"{WG_CONFIG_NAME}.conf")
    config_data = generate_client_wireguard_config(user)
    return send_file(
        BytesIO(config_data.encode()),
        download_name=filename,
        as_attachment=True,
    )


@app.route("/silent/setup.sh")
def silent_setup():
    server_url = INTRA_URL or f"http://{ensure_endpoint_ip()}:{INTRA_PORT}"
    return render_template(
        "setup.sh",
        SERVER_URL=server_url,
        WG_CONFIG_NAME=WG_CONFIG_NAME,
    )


if __name__ == "__main__":
    init_app().run(host="0.0.0.0", port=INTRA_PORT, debug=True)
