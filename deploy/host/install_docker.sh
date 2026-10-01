#!/bin/sh
# Installs Docker Engine (not Docker Desktop) inside the R6Host distro, plus
# the egress firewall that keeps containers off your LAN and your PC.
# Safe to run again.
set -eu
export DEBIAN_FRONTEND=noninteractive
HERE="$(cd "$(dirname "$0")" && pwd)"

apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg iptables

install -m 0755 -d /etc/apt/keyrings
if [ ! -s /etc/apt/keyrings/docker.asc ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
fi
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list

apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

install -m 0644 "$HERE/daemon.json" /etc/docker/daemon.json

# Re-apply the egress rules every time dockerd (re)starts.
install -d /opt/r6/host
install -m 0755 "$HERE/r6-egress.sh" /opt/r6/host/r6-egress.sh
install -d /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/r6-egress.conf <<'EOF'
[Service]
ExecStartPost=/opt/r6/host/r6-egress.sh
EOF
systemctl daemon-reload
systemctl enable docker >/dev/null 2>&1
systemctl restart docker

docker --version
docker compose version
