# TLS, names and certificates

What the deployment looks like from the network, why each piece is where it is,
and the alternatives that were weighed and dropped. Written after tracing it end
to end on 2026-09-09; every number below came from a command, not from memory.

---

## The machines

| Address | What runs there |
| --- | --- |
| `10.66.66.11` | DNS for the `.home` zone, Nginx Proxy Manager (openresty), Gitea, Nextcloud |
| `10.66.66.16` | The Servitor VM: the API, the frontend, the MCP servers |
| `batata` | Holds the mkcert CA, including its private key |
| `10.66.66.14` | The laptop |

`.home` names resolve through `10.66.66.11`:

```
$ resolvectl status        DNS Servers: 10.66.66.11
$ getent hosts ai.home     10.66.66.11
```

`ai.home` points at the proxy manager, not at the VM. That is the shape a
reverse proxy is supposed to have.

### Two paths to the same machines

```
$ ip route get 10.66.66.16    dev wg0
$ ip route get 192.168.0.19   dev wlp2s0
```

The hosts sit on WireGuard *and* on the home LAN. Nothing forces traffic
through the tunnel, so "it is already encrypted by WireGuard" is not a safe
assumption for a hop configured by address. That is the reason the hop from
the proxy manager to the VM carries TLS of its own.

---

## The certificate authority

```
subject = O=mkcert development CA, OU=vitor@batata, CN=mkcert vitor@batata
sha256  = 06:29:54:91:2F:3D:32:0C:DF:F1:48:C9:DD:33:38:C3:69:20:8C:D2:D3:9E:CD:66:AA:F1:3A:37:41:26:34:7B
```

`OU=vitor@batata` says where it was made, which is where its private key lives.
The laptop only has the public half, installed at
`/etc/ca-certificates/trust-source/anchors/myhomeca.crt`. Issue certificates on
`batata`; copying `rootCA-key.pem` around means more places that can mint a
certificate for any name your devices trust.

---

## What a certificate is valid for

The Subject Alternative Name, and nothing else. The CN has been ignored by
clients for years. From the live Nextcloud certificate:

```
$ openssl s_client -connect nextcloud.home:443 | openssl x509 -noout -subject -ext subjectAltName
subject = O=mkcert development certificate, OU=vitor@batata
X509v3 Subject Alternative Name:
    DNS:nextcloud.home
```

One argument to `mkcert`, one entry. The list is signed along with the rest, so
adding a name later means a new certificate.

**The client checks the SAN against what it dialled.** Demonstrated against a
certificate carrying only `DNS:ai.home`:

| How it was dialled | Result |
| --- | --- |
| by name | `200` |
| by address | `no alternative certificate subject name matches target ipv4 address` |
| by address, name forced (`--connect-to`, the client-side twin of `proxy_ssl_name`) | `200` |

A certificate with no `subjectAltName` at all is refused by every current
client, whatever its CN says.

---

## The two hops

```
browser --HTTPS--> proxy manager (.11) --HTTPS--> 10.66.66.16:8443 --> nginx
                                                                       |- /      the built UI
                                                                       '- /api/  backend:8000
```

The browser dials `ai.home`; the proxy dials `10.66.66.16`. One certificate
covers both:

```bash
mkcert ai.home 10.66.66.16
```

### Where each file goes

```
batata  (keeps rootCA-key.pem, which never leaves)
  |
  |- ai.home.pem + ai.home-key.pem --+--> .11  uploaded in the proxy manager
  |                                  '--> .16  ~/.config/servitor/
  |
  '- rootCA.pem -----------------------> .11  bind-mounted into its container
```

The VM never needs the CA: it presents a certificate, it does not verify
anyone. It would only need one under mutual TLS, which `nginx/nginx.conf`
carries commented out.

Reusing one pair puts the private key on two machines. Two certificates -- one
for the name, one for the address -- keep each key to one host, at the cost of
a second renewal.

### On the proxy manager

Scheme `https`, host `10.66.66.16`, port `8443`. Under SSL: the certificate,
Force SSL on, HSTS **off** -- with a private CA, a certificate problem would
lock you out for the length of `max-age`. Under Advanced:

```nginx
proxy_ssl_verify on;
proxy_ssl_trusted_certificate /etc/npm-ca/myhomeca.crt;
```

The CA has to be *bind-mounted* into the proxy manager's container. `docker cp`
survives until the next `up -d` recreates it, then the file is gone and the
config fails to reload.

Without those two lines the hop is encrypted and unverified -- the same hole as
`verify=False` in Python, only quieter.

---

## Direction, because it is the thing people get backwards

**The client validates. The server presents.** So:

- the proxy manager, dialling the VM, is a client there: it needs the CA
- the VM, answering it, is a server: it needs a certificate and a key
- FastAPI would need a CA only under mutual TLS, where it checks *client*
  certificates -- see `docs/docker.md` for where a certificate on the app
  itself earns its keep

A proxy that terminates TLS and forwards the identity in a header is asking you
to trust the header; anything that can reach the app can forge it. Terminating
at the app makes the identity cryptographic instead.

---

## Reaching the app over TLS from code

Nothing in this repository trusts a certificate blindly. Three clients, three
APIs, same idea -- the anchor is set once on the client, never per request:

| Client | Where |
| --- | --- |
| Nextcloud CalDAV (`requests.Session`) | `session.verify = _default_ca_bundle()` in `nextcloud_tasks.py` |
| MCP over HTTPS (`httpx.AsyncClient`) | `httpx_client_factory` in `client2.py:_open_endpoint` |
| Home Assistant (websocket) | `ssl.create_default_context(cafile=...)` in `home_assistant_energy.py` |

`verify=<path>` is not `verify=False`: signature, hostname and expiry are all
still checked, against a different anchor.

Two traps, both hit in practice:

- A `~` in a CA path is handed straight to OpenSSL, which does not expand it
  and raises a bare `OSError` -- not an `SSLError`, so the handler misses it.
  Every CA path in this repository is expanded before use.
- A TLS error that names neither the bundle nor the host cannot distinguish
  "wrong CA" from "no CA". The Nextcloud error names the bundle it used.

Non-browser clients cannot click through a warning. `httpx` raises and stops.
That is why a private CA installed in the system trust store beats a
self-signed certificate: with the CA, those clients need no code change at all.

---

## Which devices see a warning

Only those without the CA.

| | Behaviour |
| --- | --- |
| Linux / macOS / Windows | system trust store covers most clients |
| Firefox | its own store (NSS); `mkcert -install` needs `certutil` and an existing profile |
| Android | installs as a *user* CA: browsers honour it, apps ignore it since Android 7 |
| iOS | install the profile **and** enable it under Settings > General > About > Certificate Trust Settings |
| A visitor's device | warning, no way around it |

Clicking through is not the cheaper path: the exception is stored per device
*and per service*, it disappears when a profile is cleared, and it does nothing
for the clients that never prompt.

To touch no devices at all the certificate has to come from a CA they already
trust, which means a public name and a DNS-01 challenge -- the challenge
validates a TXT record, so the host stays unreachable from the internet while
the name resolves to a private address. `.home` can never have one.

---

## Considered and dropped

**Traefik or Caddy on the VM, routing by container label.** Genuinely solves
the "shared config breaks other stacks" problem, because each stack declares
its own route in its own compose file. Dropped because label discovery needs
`/var/run/docker.sock` mounted into a network-facing container, and socket
access is equivalent to root on the host. A socket proxy narrows it, at the
cost of another component to reduce a risk that was optional to begin with.

**A self-signed certificate.** Strictly worse than the CA for the same effort:
identical warning on machines without the CA, plus a warning on the laptop that
already trusts the CA, plus every non-browser client needs an explicit
`verify=/path`. Its one real advantage is pinning -- with no CA above it,
`proxy_ssl_trusted_certificate` pointing at the leaf accepts that certificate
and nothing else, so a leaked CA key cannot mint a substitute. Defensible for a
single-client hop, useless for the browser.

**A second DNS name for the VM (`app.home`).** A second record and a second
certificate to buy what `proxy_ssl_name`, or an address in the SAN, already
gives.

**Terminating TLS only at the proxy manager.** Was the plan until the routing
table showed both hosts sitting on the LAN as well as on WireGuard.
