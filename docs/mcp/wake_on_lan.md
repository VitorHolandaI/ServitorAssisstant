# wake_on_lan

Sends a Wake-on-LAN magic packet to power on a machine.

Uses a pure Python UDP broadcast — no `etherwake`, no root required.

## Configuration

Set in `api/.env`:

```env
WAKE_MAC=your:mac:here
```

## How it works

Sends 6 × `0xFF` followed by the target MAC repeated 16 times to `255.255.255.255:9` (standard WoL magic packet).

## Docker note

`255.255.255.255` stays inside Docker's virtual network. See [next-steps.md](../next-steps.md) for the planned `WAKE_BROADCAST` fix.

## Example prompts

- "Wake up the server"
- "Turn on my machine"
- "Send a wake on lan packet"
