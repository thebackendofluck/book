# poker-client

Windows desktop client for the virtual casino poker backend in
`../poker/game_server.py` and `../online-poker-system/`.

Stack: Electron + TypeScript. Cross-builds to Windows from macOS/Linux via
`electron-builder`.

## Layout

```
poker-client/
├── bin/                   # Symlinks/copies of the built exes after release
├── lib/
│   ├── graphics/          # HTML-Canvas renderer; future DirectX/Vulkan/Metal abstraction
│   ├── network/           # WebSocket + TLS 1.3 transport with pin callback
│   └── security/          # Cert pinning, file integrity, update signature
├── data/
│   ├── assets/            # Card images, chips, sound (fill in later)
│   ├── config/default.json# Endpoint URLs, pinned SHA-256, crash reporter URL
│   └── cache/             # Runtime cache (gitignored)
├── src/
│   ├── main/              # poker.exe entry (main + preload)
│   ├── renderer/          # UI (HTML + TS)
│   ├── updater/           # updater.exe entry
│   └── crash-reporter/    # crash-reporter.exe entry
├── build/                 # electron-builder config
├── tests/                 # vitest units + smoke harness
└── release/               # build output (created by electron-builder)
```

## Build

```bash
npm install
npm run build:ts                 # tsc -> dist/
npm run build:portable           # -> release/poker-client-0.1.0-x64-portable.exe
npm run build:nsis               # -> release/poker-client-0.1.0-x64.exe (installer)
```

Building Windows targets on macOS/Linux requires no Wine for `portable` /
`nsis` — `electron-builder` ships wine-less equivalents. If it complains,
install Wine or run on Linux.

## Test

```bash
npm test                   # vitest: pinning, connect
npm run build:ts && node tests/smoke.js   # mock-server smoke
```

## Windows VM smoke test (ops-host)

Uses `dockur/windows` with KVM passthrough. The shared folder is auto-mounted
inside Windows as `E:\`.

```bash
ssh ops-host
mkdir -p ~/poker-client-test/{shared,data}
cd ~/poker-client-test
# copy built portable exe
scp user@mac:/.../release/poker-client-0.1.0-x64-portable.exe ./shared/poker.exe
docker compose up -d
# wait ~10-40 min for first install; check http://ops-host:8006 (web VNC)
# dockur auto-runs /oem/install.bat which copies poker.exe into C:\poker\ and launches it
```

`docker-compose.yml`:

```yaml
services:
  windows:
    image: dockurr/windows
    environment:
      VERSION: "tiny11"
      RAM_SIZE: "4G"
      CPU_CORES: "2"
      DISK_SIZE: "40G"
    devices:
      - /dev/kvm
    cap_add:
      - NET_ADMIN
    ports:
      - 8006:8006
      - 3389:3389/tcp
    volumes:
      - ./data:/storage
      - ./shared:/oem
    restart: no
```

`shared/install.bat`:

```bat
@echo off
mkdir C:\poker
copy /Y E:\poker.exe C:\poker\poker.exe
start "" C:\poker\poker.exe
```

Smoke verification: screenshot `http://ops-host:8006/` shows the poker window.

### Fallback smoke test (if Windows VM too slow)

- On Linux host: `wine release/poker-client-*-portable.exe` (requires wine).
- On Mac/Linux dev: `npm run smoke` spins a mock WS server, launches the
  Electron main with `POKER_SMOKE=1`, and asserts the window opens cleanly.

## Config

Edit `data/config/default.json`. The SHA-256 pin must be the lowercase-hex
digest of the server certificate's DER encoding. Get it with:

```bash
openssl s_client -connect poker.acmetocasino.com:443 -servername poker.acmetocasino.com </dev/null 2>/dev/null \
  | openssl x509 -outform der \
  | openssl dgst -sha256 -hex
```

## Security notes

- TLS 1.3 minimum, enforced in `lib/network/connect.ts`
- Cert pinning checked in Node's `checkServerIdentity` hook
- Renderer has `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`
- CSP hard-locks script-src to `'self'`
- Crash dumps never uploaded unless `--consent` is passed to `crash-reporter.exe`

## CI

Run `npm run lint` (tsc --noEmit) and `npm test` before commits.
