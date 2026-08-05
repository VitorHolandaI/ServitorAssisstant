# ESP32-S3 WireGuard (relógio/T-Watch) — como funciona

Resumo prático de como o relógio (ESP32-S3, ESP-IDF + FreeRTOS + lwIP) sobe um
túnel WireGuard para a rede de casa (servidor `10.66.66.16`).

## Regra de ouro

**Chave privada nunca viaja.** Cada lado gera seu próprio par de chaves e só as
**públicas** são trocadas.

```
servidor 10.66.66.16          relógio (ESP32-S3)
  server.key (fica lá)          esp32.key (fica no firmware/NVS)
  server.pub ─────────────────► (gravada no firmware)
  ◄──────────────────────────── esp32.pub
```

## Bibliotecas

- **`ciniml/wireguard-lwip-esp32`** (ESP-IDF, fork do
  `smartalock/wireguard-lwip`, BSD-3) — escolhida: funciona no ESP32-S3
  (confirmado em fórum da Espressif).
- Alternativas: `ciniml/WireGuard-ESP32-Arduino` (core Arduino, parado desde
  2022, **não** compila no core 3.x — usa `tcpip_adapter.h`), `trombik/esp_wireguard`
  (ESP-IDF, alpha, WiFi only).

## API (wireguardif.h)

```c
static struct netif wg_netif;
struct wireguardif_init_data wg;
wg.private_key = "...";        // base64 da chave privada do relógio
wg.listen_port = 51820;

netif_add(&wg_netif, &ip, &netmask, &gw, &wg, wireguardif_init, ip_input);
netif_set_up(&wg_netif);

struct wireguardif_peer peer;
wireguardif_peer_init(&peer);
peer.public_key   = "...";     // chave pública do servidor
peer.allowed_ip   = allowed_ip;    // IP do túnel (ex: 10.66.66.100)
peer.allowed_mask = allowed_mask;
peer.endpoint_ip  = peer_ip;       // 10.66.66.16
peer.endport_port = 51820;
peer.keep_alive   = 25;            // atravessa NAT

u8_t idx;
wireguardif_add_peer(&wg_netif, &peer, &idx);
wireguardif_connect(&wg_netif, &idx);
// wireguardif_peer_is_up(&wg_netif, idx, &ip, &port) == ERR_OK → túnel no ar
```

Observações:
- O repo entrega só o componente (`src/`) + `example/wireguard-platform.c`;
  implementar o `wireguard-platform`: aleatório + **`wireguard_tai64n_now()`**
  (timestamp que só cresce — exatamente o relógio do dispositivo).
- **Sync de tempo antes do túnel:** `esp_sntp` via NTP normal primeiro; o
  handshake WG exige os dois peers com relógio sincronizado.
- A lib não gera chaves — gerar fora e gravar no firmware/NVS.

## Setup do servidor (10.66.66.16)

```bash
# gerar par do servidor (uma vez)
wg genkey > /etc/wireguard/server.key
wg pubkey < /etc/wireguard/server.key > /etc/wireguard/server.pub
```

Adicionar peer no `wg0.conf` (ou `wg set wg0 peer ...`):

```
[Peer]
PublicKey = <esp32.pub>          # copiada da máquina de desenvolvimento
AllowedIPs = 10.66.66.100/32     # IP de túnel dedicado ao relógio
PersistentKeepalive = 25
```

Aplicar sem reiniciar: `wg addconf wg0 <(wg-quick strip wg0)`.

## Lado do relógio (gerado na máquina de dev)

```bash
wg genkey > esp32.key
wg pubkey < esp32.key > esp32.pub
```

- `esp32.key` → firmware/NVS do S3 (`wireguardif_init_data.private_key`).
- `esp32.pub` → copiar para o servidor (peer acima).
