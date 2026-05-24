# Boot-time Autostart Setup

The `compose.yaml` stack is managed by a systemd service so it starts automatically on boot.

## Install the service

Create `/etc/systemd/system/dgx-llm-server.service`:

```ini
[Unit]
Description=DGX LLM Server (llama.cpp docker compose stack)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/zbloss/Projects/dgx-llm-server
EnvironmentFile=/home/zbloss/Projects/dgx-llm-server/.env
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

## Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dgx-llm-server.service
```

## Verify

```bash
sudo systemctl status dgx-llm-server.service
```

## Notes

- The service waits for Docker and the network before starting.
- `TimeoutStartSec=300` allows up to 5 minutes for image pulls and GPU initialization on first boot.
- `EnvironmentFile` loads `.env` so `LLAMA_API_KEY` is available to compose.
- Container-level restarts are handled by `restart: unless-stopped` in `compose.yaml`; this service handles the initial boot-time bring-up.
