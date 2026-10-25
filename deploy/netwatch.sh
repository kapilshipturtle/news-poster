#!/bin/bash
# Live 2026-10-25: after an out-of-memory kill the VM lost the metadata server (SSH keys, the Cloud Run identity token)
# and DNS for 80 min, until a manual reset. Every 5 min: both reachable? 3 failures in a row (15 min) -> reboot, never
# twice within an hour.
F=/var/lib/news-netwatch.fails
if curl -s -m 8 -H 'Metadata-Flavor: Google' http://169.254.169.254/computeMetadata/v1/instance/id >/dev/null \
   && getent hosts generativelanguage.googleapis.com >/dev/null; then
  echo 0 > "$F"
  exit 0
fi
n=$(( $(cat "$F" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$F"
echo "netwatch: metadata server or DNS unreachable ($n in a row)"
if [ "$n" -ge 3 ] && [ "$(cut -d. -f1 /proc/uptime)" -gt 3600 ]; then
  echo 0 > "$F"
  echo "netwatch: rebooting to recover the network"
  systemctl reboot
fi
