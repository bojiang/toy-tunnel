#!/bin/bash

# 1. Check platform, install WireGuard, and wget
if [[ "$(uname)" == "Linux" ]]; then
    # Install WireGuard based on different Linux distributions
    if [[ -x "$(command -v apt-get)" ]]; then
        apt-get update
        apt-get install -y wireguard wget
    elif [[ -x "$(command -v yum)" ]]; then
        yum install -y epel-release
        yum install -y wireguard-tools wget
    else
        echo "Unable to determine the Linux distribution or find a suitable package manager to install WireGuard. Please install WireGuard and wget manually."
        exit 1
    fi
else
    echo "Unsupported operating system. Please install WireGuard and wget manually."
    exit 1
fi

# 2. Download configuration file
wget -O /etc/wireguard/{{WG_CONFIG_NAME}}.conf {{SERVER_URL}}/silent/download-config

# 3. Start WireGuard using systemd
if [[ -x "$(command -v systemctl)" ]]; then
    systemctl enable wg-quick@{{WG_CONFIG_NAME}}.service
    systemctl start wg-quick@{{WG_CONFIG_NAME}}.service
    echo "WireGuard has been successfully started."
else
    echo "Systemd is not available on this system. Please start the WireGuard service manually."
fi

