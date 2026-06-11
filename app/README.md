# DepthFusion Desktop App

Tauri 2 + React + TypeScript + Tailwind CSS scaffold.

## Prerequisites

Install Rust and system dependencies:

```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install Tauri system dependencies on Ubuntu/Debian
sudo apt update
sudo apt install -y libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev
```

## Run

```bash
npm install && cargo tauri dev
```

## Build

```bash
npm run build
cargo tauri build
```

## Stack

- Tauri 2 (desktop shell, identifier: `com.depthfusion.app`)
- React 19 + TypeScript (strict mode)
- Tailwind CSS 4 (via `@tailwindcss/vite` plugin)
- Vite dev server on port 1420
