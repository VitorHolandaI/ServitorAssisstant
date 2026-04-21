# Next Steps

## Proxmox integration

- [ ] Install `proxmoxer` dependency
  ```bash
  pip install proxmoxer
  ```
- [ ] Add Proxmox env vars to `api/.env.example`
  ```env
  PROXMOX_HOST=192.168.0.1
  PROXMOX_USER=user@pam
  PROXMOX_TOKEN_NAME=tokenid
  PROXMOX_TOKEN_VALUE=secret
  PROXMOX_NODE=pve
  PROXMOX_VMID=100
  ```
- [ ] Create API token in Proxmox UI: `Datacenter → Permissions → API Tokens → Add`
- [ ] Add `shutdown_server` MCP tool to `stream2.py`
- [ ] Add `start_server` MCP tool to `stream2.py`
- [ ] Add `server_status` MCP tool to `stream2.py` (returns VM power state)

---

## Docker — Wake-on-LAN broadcast fix

WoL from inside a Docker container does not work with `255.255.255.255` because
the broadcast stays inside Docker's virtual network and never reaches the physical LAN.

- [ ] Create branch `feat/wol-docker-broadcast`
- [ ] Add `WAKE_BROADCAST` env var (default `255.255.255.255`)
  ```env
  # Set to your LAN subnet broadcast when running in Docker
  # e.g. if your LAN is 192.168.0.0/24 use 192.168.0.255
  WAKE_BROADCAST=255.255.255.255
  ```
- [ ] Update `wake_on_lan` tool in `stream2.py` to use `WAKE_BROADCAST`
- [ ] Add `WAKE_BROADCAST` to `docker-compose.yml` environment block for `mcp` service
- [ ] Test from inside container and confirm packet reaches physical LAN

---

## Docker — general improvements

- [ ] Add `healthcheck` to `backend` and `mcp` services in `docker-compose.yml`
- [ ] Add `depends_on` condition `service_healthy` on `nginx` so it only starts when backend is ready
- [ ] Add GPU passthrough to `ollama` service for faster inference
  ```yaml
  ollama:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
  ```
