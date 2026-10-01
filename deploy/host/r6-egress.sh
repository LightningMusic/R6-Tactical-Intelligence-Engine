#!/bin/sh
# Egress firewall for containers.
#
# A container may talk to the internet and to other containers, and nothing
# else: not your LAN, not the Windows host, not this VM, not the tailnet.
# So a compromised server container can't be used to poke at the rest of the
# house. Docker's address pool is pinned to 10.77.0.0/16 (see daemon.json).
set -eu
POOL=10.77.0.0/16

iptables -w -N DOCKER-USER 2>/dev/null || true
iptables -w -F DOCKER-USER

# container <-> container
iptables -w -A DOCKER-USER -s "$POOL" -d "$POOL" -j RETURN

# container -> any private / link-local / CGNAT (Tailscale) / multicast range
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10 127.0.0.0/8 224.0.0.0/4 240.0.0.0/4; do
    iptables -w -A DOCKER-USER -s "$POOL" -d "$net" -j REJECT --reject-with icmp-admin-prohibited
done
iptables -w -A DOCKER-USER -j RETURN

# container -> services on this VM itself (nothing legitimate needs them)
iptables -w -C INPUT -s "$POOL" -m conntrack --ctstate NEW -j DROP 2>/dev/null \
    || iptables -w -I INPUT 1 -s "$POOL" -m conntrack --ctstate NEW -j DROP

echo "[r6-egress] rules applied"
