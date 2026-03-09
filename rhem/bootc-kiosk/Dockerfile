FROM registry.redhat.io/rhel9/rhel-bootc:9.7

# Must be 9.7. Because the podman is super old on 9.4 and throws confusing error.

# --- RHSM (only if you need it during build; otherwise remove) ---
RUN subscription-manager register --activationkey=xxxxxxx --org=xxxxxx && \
    subscription-manager status

# --- Enable EPEL (Chromium on EL9 is typically from EPEL) ---
RUN dnf -y install https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm && \
    dnf -y makecache && \
    dnf clean all

# --- Packages: httpd + kiosk stack (Xorg + startx + Chromium) + SELinux tooling ---
RUN dnf -y install --setopt=install_weak_deps=False \
      httpd \
      chromium \
      xorg-x11-server-Xorg \
      xorg-x11-xinit \
      xorg-x11-xauth \
      xorg-x11-server-utils \
      dbus-daemon \
      curl \
      cloud-init \
      open-vm-tools \
      policycoreutils \
      policycoreutils-python-utils \
      selinux-policy-targeted \
    && dnf clean all

# --- Flightctl agent repo + install ---
RUN dnf -y install dnf-plugins-core && dnf clean all
RUN dnf config-manager --add-repo https://rpm.flightctl.io/flightctl-epel.repo
RUN dnf -y install flightctl-agent && dnf clean all
RUN mkdir -p /etc/flightctl/
ADD config.yaml /etc/flightctl/config.yaml

# --- Cloud-init: stop EC2 probing on QEMU ---
RUN mkdir -p /etc/cloud/cloud.cfg.d && \
    cat > /etc/cloud/cloud.cfg.d/99-datasource.cfg <<'EOF'
datasource_list: [ NoCloud, None ]
EOF

# --- Apache: content ---
RUN mkdir -p /var/lib/kiosk/html/ && \
    cat > /var/lib/kiosk/html/index.html <<'EOF'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Kiosk</title></head>
<body><h1 style="text-align:center;">Initial BootC RHEL</h1></body>
</html>
EOF

# Symlink the standard Apache path to our persistent location
# This ensures /var/www/html (read-only) points to /var/lib/kiosk/html (writable)
RUN rm -rf /var/www/html
RUN chmod 777 -R /var/lib/kiosk/html/
RUN ln -s /var/lib/kiosk/html /var/www

# --- Apache: log files in /var/log/httpd ---
RUN mkdir -p /var/log/httpd && \
    sed -ri \
      -e 's|^ErrorLog .*|ErrorLog /var/log/httpd/error_log|' \
      -e 's|^CustomLog .*|CustomLog /var/log/httpd/access_log combined|' \
      /etc/httpd/conf/httpd.conf

# --- SELinux: correct contexts ---
# We target the actual path in /var/lib/kiosk/html
RUN semanage fcontext -a -t httpd_sys_content_t '/var/lib/kiosk/html(/.*)?' && \
    semanage fcontext -a -t httpd_log_t '/var/log/httpd(/.*)?' && \
    restorecon -Rv /var/lib/kiosk /var/log/httpd || true



RUN cat > /etc/containers/auth.json <<'EOF'
{
        "auths": {
                "quay.io/alitestseverything": {
                        "auth": "base64 encoded (username:token)"
                }
        }
}    
EOF

# --- Kiosk user ---
RUN useradd -m -G wheel kiosk && \
    echo 'kiosk:kiosk' | chpasswd

# --- Autologin on tty1 ---
RUN mkdir -p /etc/systemd/system/getty@tty1.service.d/ && \
    cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin kiosk --noclear %I $TERM
EOF


# --- Script to refresh/relaunch Chromium ---
RUN cat > /usr/local/bin/kiosk-refresh.sh <<'EOF'
#!/bin/bash
# Find the Chromium process running as the kiosk user and terminate it.
# The kiosk-session.sh loop will automatically restart it.
pkill -u kiosk -f chromium || echo "Chromium not running."
EOF
RUN chmod +x /usr/local/bin/kiosk-refresh.sh

# --- Kiosk session command (Chromium opens httpd) ---
# IMPORTANT: On EPEL/RHEL-like setups the executable is often /usr/bin/chromium-browser (not /usr/bin/chromium),
# so we detect it at runtime. [web:378]
RUN cat > /usr/local/bin/kiosk-session.sh <<'EOF'
#!/bin/bash
set -euo pipefail

# Detect resolution from xrandr
# We wait a moment for X to initialize the display
sleep 2
WIDTH=$(xrandr | grep '*' | awk '{print $1}' | cut -d 'x' -f1)
HEIGHT=$(xrandr | grep '*' | awk '{print $1}' | cut -d 'x' -f2)

URL="http://127.0.0.1/"

BROWSER="$(command -v chromium-browser || true)"
if [[ -z "${BROWSER}" ]]; then
  BROWSER="$(command -v chromium || true)"
fi
if [[ -z "${BROWSER}" ]]; then
  echo "No chromium binary found (expected chromium-browser or chromium)" >&2
  exit 1
fi

while true; do
  rm -rf /tmp/chromium-kiosk-profile || true
  /usr/bin/dbus-run-session -- "${BROWSER}" \
    --kiosk \
    --no-first-run \
    --window-position=0,0 \
    --window-size="${WIDTH},${HEIGHT}" \
    --force-device-scale-factor=1 \
    --disable-session-crashed-bubble \
    --disable-infobars \
    --disable-notifications \
    --user-data-dir=/tmp/chromium-kiosk-profile \
    "${URL}" \
    || true
  sleep 1
done
EOF
RUN chmod +x /usr/local/bin/kiosk-session.sh

# --- systemd unit: start X rootless on VT1 with logind session ---
RUN cat > /etc/systemd/system/kiosk.service <<'EOF'
[Unit]
Description=Kiosk (Xorg + Chromium)
Wants=network-online.target httpd.service
After=systemd-user-sessions.service network-online.target httpd.service
StartLimitIntervalSec=0

[Service]
User=kiosk
WorkingDirectory=/var/home/kiosk
PAMName=login
Environment=XDG_SESSION_TYPE=x11
Environment=DISPLAY=:0

TTYPath=/dev/tty8
StandardInput=tty
StandardOutput=journal
StandardError=journal
UnsetEnvironment=TERM
UtmpIdentifier=tty8
UtmpMode=user

ExecStartPre=/usr/bin/chvt 8
ExecStartPre=/bin/bash -lc 'for i in $(seq 1 60); do curl -fsS http://127.0.0.1/ >/dev/null 2>&1 && exit 0; sleep 1; done; exit 0'
ExecStart=/usr/bin/startx /usr/local/bin/kiosk-session.sh -- vt8 -keeptty -nolisten tcp -verbose 3

Restart=always
RestartSec=2

[Install]
WantedBy=graphical.target
EOF

# --- Enable services ---
RUN systemctl enable httpd && \
    systemctl enable flightctl-agent.service && \
    systemctl enable kiosk.service && \
    systemctl enable vmtoolsd.service

RUN systemctl set-default graphical.target

# --- Cleanup ---
RUN rm -rf /var/cache/* /var/tmp/*
