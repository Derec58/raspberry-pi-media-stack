# ops/ — host-level files

These live outside the compose stack and are **not** deployed by
`docker compose`. They are tracked here because they previously existed only on
the host, in no repository, which meant a rebuild from `git clone` would have
silently lost the backup system, the mount guard and the LAN-only firewall
rules.

## Install locations

| Repo path | Installed to |
| --- | --- |
| `ops/pi-backup.sh` | `/usr/local/sbin/pi-backup.sh` (root:root, 0755) |
| `ops/systemd/*.service`, `ops/systemd/*.timer` | `/etc/systemd/system/` |

After copying, run `sudo systemctl daemon-reload`, then enable the timers:

```bash
sudo systemctl enable --now pi-backup.timer
sudo systemctl enable --now media-stack-mount-guard.timer
sudo systemctl enable --now media-stack-lan-only-ports.timer
sudo systemctl enable --now media-stack-vpn-sync.service
```

## What each one does

- **pi-backup** — weekly cold backup to `/mnt/backup`. Stops Jellyfin and the
  compose stack first so every SQLite database is captured consistent, then
  restarts them with a retry. Guards on `/mnt/backup` being a real mountpoint
  *before* stopping anything, so a missing backup drive aborts harmlessly.
- **media-stack-mount-guard** — detects and heals containers pinned to a stale
  media bind mount after a boot race, by comparing `st_dev` rather than paths.
- **media-stack-lan-only-ports** — re-applies the `DOCKER-USER` rules that
  restrict published web UIs to RFC1918 sources. Re-asserted periodically
  because a dockerd restart rebuilds `DOCKER-USER` and drops the jump.
- **media-stack-vpn-sync** — keeps qBittorrent bound to the current tun0 IP.

## Known gaps

- `pi-backup.sh` uses `rsync --delete` against a single destination with no
  generations, so one bad run overwrites the only good copy. `--link-dest`
  snapshots are the fix.
- The unit is `Type=oneshot` with the restart wrapped in `|| true`, so a
  failure is invisible to systemd. It needs `OnFailure=`.
- Nothing on this host can send a notification: there is no MTA, and smartd
  mails root into a void.
