import functools
import ipaddress
import logging
import os
import sqlite3
import subprocess
import typing
from io import BytesIO

import firebase_admin
import firebase_admin.auth
import pathvalidate
from flask import Flask, render_template, request, send_file
from flask_cors import CORS

# Required options
FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY", "abcd1234")
FIREBASE_AUTH_DOMAIN = os.getenv("FIREBASE_AUTH_DOMAIN", "xxxx.firebaseapp.com")
FIREBASE_ADMIN_CRIDENTIAL = os.getenv("FIREBASE_ADMIN_CRIDENTIAL", "run/token.json")
EMAIL_SUFFIX = os.getenv("EMAIL_SUFFIX", "")

# Optional options
EXTERNAL_IP = os.getenv("EXTERNAL_IP", "")
VPN_NETWORK = os.getenv("VPN_NETWORK", "10.7.0.0/16")
VPN_GATEWAY = os.getenv("VPN_GATEWAY", "10.7.0.0/16")
RESERVED_IP_COUNT = int(os.getenv("RESERVED_IP_COUNT", "10"))

# other constants
WG_CONFIG_DIR = "/etc/wireguard"
WG_CONFIG_NAME = "wg0"
DB_FILE = f"run/users.db"



NETWORK = ipaddress.IPv4Network(VPN_NETWORK)
GATEWAY = VPN_GATEWAY and ipaddress.IPv4Network(VPN_GATEWAY) or NETWORK

assert GATEWAY.subnet_of(NETWORK), "vpn network must be a subnet of vpn gateway"

app = Flask(__name__)
CORS(app)


@functools.lru_cache(maxsize=1)
def ensure_endpoint_ip():
    ip = EXTERNAL_IP
    if ip == "":
        ip = subprocess.check_output(["curl", "http://checkip.amazonaws.com"]).decode("utf-8").strip()
    if not ipaddress.ip_address(ip).is_global:
        logging.warning("Endpoint IP %s is not a global IP", ip)
    logging.info("Endpoint IP: %s", ip)
    return ip


def ensure_db_file():
    if os.path.exists(DB_FILE):
        logging.info("Loading user table")
        return DB_FILE
    logging.info("Creating user table")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_id TEXT NOT NULL,
            email TEXT NOT NULL,
            private_key TEXT NOT NULL,
            public_key TEXT NOT NULL
        )
    """
    )
    conn.commit()
    conn.close()
    return DB_FILE


User = typing.NamedTuple(
    "User",
    [
        ("id", int),
        ("google_id", str),
        ("email", str),
        ("private_key", str),
        ("public_key", str),
    ],
)


def ensure_server_user():
    server_user = get_user("0")
    if server_user is not None:
        return server_user
    logging.info("Creating server user")
    conn = sqlite3.connect(ensure_db_file())
    cursor = conn.cursor()
    private_key = subprocess.check_output(["wg", "genkey"]).decode().strip()
    public_key = (
        subprocess.check_output(["wg", "pubkey"], input=private_key.encode())
        .decode()
        .strip()
    )
    cursor.execute(
        "INSERT INTO users (id, google_id, email, private_key, public_key) VALUES (?, ?, ?, ?, ?)",
        (0, "0", "0", private_key, public_key),
    )
    conn.commit()
    conn.close()
    return User(0, "0", "0", private_key, public_key)


def add_user(google_id, email=""):
    conn = sqlite3.connect(ensure_db_file())
    cursor = conn.cursor()
    private_key = subprocess.check_output(["wg", "genkey"]).decode().strip()
    public_key = (
        subprocess.check_output(["wg", "pubkey"], input=private_key.encode())
        .decode()
        .strip()
    )
    cursor.execute(
        "INSERT INTO users (google_id, email, private_key, public_key) VALUES (?, ?, ?, ?)",
        (google_id, email, private_key, public_key),
    )
    conn.commit()
    conn.close()
    sync_wireguard()
    return get_user(google_id)


def get_user(google_id):
    conn = sqlite3.connect(ensure_db_file())
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, google_id, email, private_key, public_key FROM users WHERE google_id = ?",
        (google_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return User(*row)


def ensure_user(google_id, email=""):
    user = get_user(google_id)
    if user is None:
        logging.info("Adding user %s, %s", google_id, email)
        return add_user(google_id, email)
    return user


def get_ip_by_user_id(user_id):
    return NETWORK[RESERVED_IP_COUNT + user_id]


def generate_client_wireguard_config(user):
    endpoint_ip = ensure_endpoint_ip()
    client_ip = get_ip_by_user_id(user.id)
    server_user = ensure_server_user()
    assert client_ip < NETWORK.broadcast_address, "Subnet full"
    return f"""[Interface]
PrivateKey = {user.private_key}
Address = {client_ip}/{NETWORK.prefixlen}

[Peer]
PublicKey = {server_user.public_key}
AllowedIPs = {GATEWAY[1]}/{GATEWAY.prefixlen}
Endpoint = {endpoint_ip}:51820
PersistentKeepalive = 25"""


def generate_server_wireguard_config():
    ensure_endpoint_ip()
    server_user = ensure_server_user()
    server_section = f"""[Interface]
PrivateKey = {server_user.private_key}
Address = {NETWORK[1]}/{NETWORK.prefixlen}
ListenPort = 51820

"""
    client_sections = []
    conn = sqlite3.connect(ensure_db_file())
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, google_id, email, private_key, public_key FROM users WHERE id > 0"
    )
    for row in cursor.fetchall():
        user = User(*row)
        client_sections.append(
            f"""[Peer]
PublicKey = {user.public_key}
AllowedIPs = {get_ip_by_user_id(user.id)}/32
"""
        )
    conn.close()
    logging.info(server_section)
    return server_section + "\n".join(client_sections)


def write_server_wireguard_config():
    config = generate_server_wireguard_config()
    if not os.path.exists(WG_CONFIG_DIR):
        logging.info("Creating Wireguard config directory")
        os.makedirs(WG_CONFIG_DIR)
    path = os.path.join(WG_CONFIG_DIR, f"{WG_CONFIG_NAME}.conf")
    logging.info("writing Wireguard config to %s", path)
    with open(path, "w") as f:
        f.write(config)


def sync_wireguard():
    write_server_wireguard_config()
    if subprocess.call(["wg", "show", WG_CONFIG_NAME], stdout=subprocess.DEVNULL):
        logging.info("Wireguard interface not found, creating")
        subprocess.check_call(["wg-quick", "up", WG_CONFIG_NAME])
    else:
        logging.info("updating wireguard config")
        subprocess.check_call(["bash", "-c", f"wg syncconf {WG_CONFIG_NAME} <(wg-quick strip {WG_CONFIG_NAME})"])


@functools.lru_cache(maxsize=1)
def ensure_firebase():
    logging.info("Initializing Firebase")
    cred = firebase_admin.credentials.Certificate(FIREBASE_ADMIN_CRIDENTIAL)
    firebase_admin.initialize_app(cred)


def verify_id_token(id_token):
    logging.info("Verifying ID token")
    try:
        ensure_firebase()
        decoded_token = firebase_admin.auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        print("Token verification failed: {0}".format(e))
        return None


@app.route("/")
def index():
    return render_template(
        "index.html",
        FIREBASE_API_KEY=FIREBASE_API_KEY,
        FIREBASE_AUTH_DOMAIN=FIREBASE_AUTH_DOMAIN,
    )


@app.route("/login")
def login():
    return render_template(
        "login.html",
        FIREBASE_API_KEY=FIREBASE_API_KEY,
        FIREBASE_AUTH_DOMAIN=FIREBASE_AUTH_DOMAIN,
    )


@app.route("/login", methods=["POST"])
def login_post():
    id_token = request.form.get("idToken")
    user_info = verify_id_token(id_token)
    if user_info:
        print(user_info)
        google_id = user_info["sub"]
        email = user_info["email"]
        if EMAIL_SUFFIX:
            if not email or email.split("@")[-1] != EMAIL_SUFFIX:
                return "No Permission, please try with your Enterprice Email", 403
        user = ensure_user(google_id, email)
        if user is None:
            return "Failed to create user", 500
        return "Success"
    else:
        return "Error", 403


@app.route("/me")
def me():
    return render_template(
        "me.html",
        FIREBASE_API_KEY=FIREBASE_API_KEY,
        FIREBASE_AUTH_DOMAIN=FIREBASE_AUTH_DOMAIN,
    )


@app.route("/me/download-config")
def download_config():
    if "Authorization" not in request.headers:
        return "Unauthorized", 401
    id_token = request.headers["Authorization"].split(" ")[1]
    print("id_token:", repr(id_token))
    user_info = verify_id_token(id_token)
    print("user_info:", user_info)
    if user_info:
        google_id = user_info["sub"]
        user = get_user(google_id)
        if user:
            filename = pathvalidate.sanitize_filename(f"config_{user.email}.conf")
            config_data = generate_client_wireguard_config(user)
            return send_file(
                BytesIO(config_data.encode()),
                download_name=filename,
                as_attachment=True,
            )
        else:
            return "User not found", 401
    else:
        return "Unauthorized", 401


def startup():
    logging.basicConfig(level=logging.INFO)
    logging.info("Starting up")
    sync_wireguard()


startup()

if __name__ == "__main__":
    init_app().run(host="0.0.0.0", port=5000, debug=True)
