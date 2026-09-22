#!/usr/bin/env bash
# Prepares a fresh Debian/Ubuntu VPS to run the stack. Run once, as root.
#
#   ssh root@<vps-ip> 'bash -s' < deploy/bootstrap-vps.sh
#
# Idempotent: safe to re-run.
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
SWAP_GB="${SWAP_GB:-4}"

log() { printf '\n==> %s\n' "$1"; }

log "Updating base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq ca-certificates curl git ufw unattended-upgrades

log "Installing Docker Engine"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

log "Creating deploy user '${DEPLOY_USER}'"
if ! id -u "${DEPLOY_USER}" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "${DEPLOY_USER}"
fi
usermod -aG docker "${DEPLOY_USER}"

# Copy in whichever authorized_keys this image shipped with. Cloud user first:
# EC2 images also leave a root entry, but it is wrapped in a forced command that
# prints 'Please login as the user "ubuntu"' and exits, which would make the
# deploy user unusable. Strip any such prefix so only the bare key survives.
AUTH_SRC=""
for candidate in /home/ubuntu /home/admin /home/ec2-user /root; do
  if [ -s "${candidate}/.ssh/authorized_keys" ]; then AUTH_SRC="${candidate}/.ssh/authorized_keys"; break; fi
done
if [ -n "${AUTH_SRC}" ]; then
  echo "    using ${AUTH_SRC}"
  install -d -m 700 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "/home/${DEPLOY_USER}/.ssh"
  sed -E 's/^.*((ssh-(rsa|dss|ed25519))|(ecdsa-sha2-[a-z0-9-]+)|(sk-(ssh-ed25519|ecdsa-sha2-[a-z0-9-]+)@openssh\.com)) /\1 /' \
    "${AUTH_SRC}" > "/home/${DEPLOY_USER}/.ssh/authorized_keys"
  chown "${DEPLOY_USER}:${DEPLOY_USER}" "/home/${DEPLOY_USER}/.ssh/authorized_keys"
  chmod 600 "/home/${DEPLOY_USER}/.ssh/authorized_keys"
else
  echo "    WARNING: no authorized_keys found - you must add one before logging out"
fi

log "Adding ${SWAP_GB}GB swap (the ai-service image build needs the headroom)"
if [ ! -f /swapfile ]; then
  fallocate -l "${SWAP_GB}G" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "Configuring firewall"
# Only SSH is reachable. Every application port is served through the
# Cloudflare Tunnel, which is an outbound-only connection.
# On EC2 the security group must allow 22/tcp as well - ufw is the inner gate.
ufw allow OpenSSH
ufw --force enable

log "Hardening SSH (key-only login)"
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload ssh || systemctl reload sshd

log "Enabling unattended security upgrades"
dpkg-reconfigure -f noninteractive unattended-upgrades

log "Done. Next: log in as '${DEPLOY_USER}' and run deploy/deploy.sh"
docker --version
