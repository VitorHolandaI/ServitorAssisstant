# Docker Setup

## Prerequisites

- Docker 24+ with Compose v2
- A Piper TTS voice model (`.onnx` + `.onnx.json`)
- ~10 GB free disk space

---

## 1. Add your voice model

Download both files into `voice_models/` (the `.json` config is required alongside the `.onnx`):

```bash
wget -P voice_models/ https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx
wget -P voice_models/ https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json
```

Result:
```
voice_models/
  en_US-ryan-medium.onnx
  en_US-ryan-medium.onnx.json
```

> Other voices available at https://huggingface.co/rhasspy/piper-voices — update `VOICE_PATH` in `.env` to match.

---

## 2. Configure

```bash
cp .env.example .env
```

Edit `.env` in the repository root — minimum required values:

```env
VOICE_PATH=/app/voice_models/en_US-ryan-medium.onnx
WAKE_MAC=your:mac:here        # only if using Wake-on-LAN
CLIENT_IP=192.168.0.22        # only if using audio mode (Raspberry Pi)
```

Everything else (Ollama URL, MCP addresses, profile) is wired between the
containers by `docker-compose.yml` and overrides whatever `.env` says.

### Files that stay out of the repository

The Home Assistant token and the private CA live in `~/.config/servitor` on the
host and are mounted read-only at `/etc/servitor`:

```
~/.config/servitor/
  home-root-ca.crt     the CA that signs nextcloud.home
  ha-root-ca.crt       the CA that signs smart.home
  ha-token             the Home Assistant bearer token
  ai.home.pem          the certificate nginx presents
  ai.home-key.pem      its key
```

The paths handed to the app must be the ones **inside** the container. `~` is
expanded against the container's HOME, which is not yours, so the compose file
sets them explicitly:

```env
NC_CA_BUNDLE=/etc/servitor/home-root-ca.crt
HOME_ASSISTANT_CA_BUNDLE=/etc/servitor/ha-root-ca.crt
HOME_ASSISTANT_TOKEN_FILE=/etc/servitor/ha-token
```

Set `SERVITOR_CONFIG_DIR` if that directory lives somewhere else.

---

## 3. Build and start

```bash
docker compose up --build
```

First run is slow: Docker builds the Python image (torch is large) and Ollama
pulls the model named by `OLLAMA_MODEL`, which the entrypoint and the
application read from the same place. Subsequent starts are fast.

Wait for:
```
[ollama] model ready
INFO:     Application startup complete.
```

Then open **http://localhost:8080**, or the name the proxy manager serves.

---

## Services

| Service  | Role | Ports |
|----------|------|-------|
| `nginx`  | Serves the built UI; proxies `/api/*` to the backend | 80 and 443, published as 8080 and 8443 |
| `backend`| FastAPI, the agent, TTS | 8000, never published |
| `mcp`    | The three servers that need only a network: general (8001), dev-activity (8002), Nextcloud (8003) | never published |
| `ollama` | Inference and model storage | never published |

Only nginx is on the wire. The browser reaches the page and the API through one
origin, which is why no `VITE_SERVER_IP` is needed and mixed content cannot
happen.

The four MCP servers that need a desktop -- dictation, browser, MPRIS, YouTube
-- are not here. They live in `servitor_local_notebook/` and run on the laptop.

---

## Persistent data

| Volume | Contents |
|--------|---------|
| `ollama-data` | Ollama models |
| `app-data` | SQLite DB (tasks + conversation history) |

`docker compose down` stops containers but keeps volumes. To wipe everything including the model:

```bash
docker compose down -v
```

---

## Useful commands

```bash
# Run in background
docker compose up -d --build

# Follow logs
docker compose logs -f

# Rebuild only one service
docker compose up -d --build backend

# Stop (keep data)
docker compose down
```

---

## Changing the port

Add to a `.env` file at the project root:

```env
HTTP_PORT=8080
```

---

## TLS, end to end

Two hops, both encrypted, one certificate.

```
browser --HTTPS--> proxy manager (.11) --HTTPS--> 10.66.66.16:8443 --> nginx
                                                                       |- /      the built UI
                                                                       '- /api/  backend:8000
```

### 1. Issue the certificate

On the machine that holds the CA -- the certificate says which, under `OU=`:

```bash
mkcert ai.home 10.66.66.16
```

The address goes in the SAN because that is what the proxy manager dials; the
name is there for the browser. A client checks the SAN against what it dialled,
so without the address the proxy would need `proxy_ssl_name ai.home` instead. A certificate without a `subjectAltName` is refused by
every current client, whatever the CN says.

### 2. Place the files

| File | Where | For |
| --- | --- | --- |
| `ai.home.pem` + `ai.home-key.pem` | uploaded in the proxy manager | shown to the browser |
| the same two | `~/.config/servitor/` on the VM | shown to the proxy manager |
| `rootCA.pem` | on the proxy manager's host, bind-mounted into its container | verifying the VM |

The CA has to be mounted into the proxy manager's container, not copied in with
`docker cp`: the next `up -d` recreates the container and the file is gone.

Confirm you carried the right CA by comparing fingerprints on both machines:

```bash
openssl x509 -in <file> -noout -fingerprint -sha256
```

### 3. Configure the proxy host

Scheme `https`, host `10.66.66.16`, port `8443`. Under SSL, pick the certificate
and turn on Force SSL. Leave HSTS off: with a private CA, a certificate problem
would lock you out for the length of `max-age`.

Under Advanced:

```nginx
proxy_ssl_verify on;
proxy_ssl_trusted_certificate /etc/npm-ca/myhomeca.crt;
```

Without those two lines the hop is encrypted and unverified, which is the same
hole as `verify=False` in Python, only quieter.

### 4. Switch over, in this order

1. `docker compose up -d --build` on the VM
2. Repoint the proxy host from `5173` to `8443`
3. Only then close 5173 and 8000

Reversing 2 and 3 leaves the site down in between.

### Which devices see no warning

Only those with the CA installed. The OS trust store covers most things;
Firefox keeps its own (NSS) and needs `mkcert -install` with a profile already
created. Android treats it as a user CA, which browsers honour and apps ignore.
iOS needs the profile installed *and* enabled under Settings > General > About >
Certificate Trust Settings.

To avoid touching devices at all, the certificate has to come from a CA they
already trust, which means a public name and a DNS-01 challenge. The proxy
manager can do that natively; `.home` can never have one.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `VOICE_PATH not set` | Check `.env` has `VOICE_PATH` and `voice_models/` is populated |
| Ollama healthcheck failing | Model is still downloading — `docker compose logs ollama` to watch |
| `Sorry, I encountered an error` | Backend/MCP not ready yet — wait and retry |
| Port 80 in use | Set `HTTP_PORT` in `.env` |
| `NC_CA_BUNDLE does not exist: /etc/servitor/...` | The config directory is not mounted, or the file is not in it |
| Nextcloud TLS verification failed against `<path>` | That bundle is not the CA that signed `NC_URL` |
| `cannot load certificate key ... Permission denied` | The key is `0600` owned by your user. `nginx:alpine` reads it as root; an unprivileged image cannot |
| Redirects come back as `http://` on an HTTPS page | `--forwarded-allow-ips` is missing, so uvicorn ignored `X-Forwarded-Proto` |
| `Permission denied` writing `/app/data` on an existing volume | The volume predates the non-root user and belongs to root. `docker compose down -v`, or `docker run --rm -v <volume>:/d alpine chown -R 1000:1000 /d` |
| 404 after refreshing a sub-page | `try_files ... /index.html` missing: a SPA serves every route from one document |
