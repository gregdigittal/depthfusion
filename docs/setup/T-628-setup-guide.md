# T-628 Setup Guide
## Getting the DepthFusion desktop app to compile on this server

You do not need to know Rust, Tauri, or TypeScript to follow this guide.
Every command is given in full. Copy and paste them exactly.

---

## What you're doing and why

The desktop app (built with a framework called **Tauri**) is already written.
The code is complete. But to *compile* it, the server needs some Linux
system libraries that aren't installed yet. This guide installs those
libraries, then confirms the app compiles cleanly.

Think of it like installing software that a program needs to run — except
in this case you're installing software that the *build tool* needs
to turn source code into a working app.

---

## Before you start — open a terminal on the server

If you're connecting via SSH, run:

```
ssh gregmorris@176.9.147.206
```

All commands below are typed into that terminal session.

---

## Step 1 — Install the missing system libraries

This is a single command. It will ask for your password (type it and press Enter).
It will download and install several packages. This takes about 1–2 minutes.

```bash
sudo apt update && sudo apt install -y \
  libwebkit2gtk-4.1-dev \
  libgdk-pixbuf2.0-dev \
  libxdo-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev
```

**What each package is:**

| Package | Why it's needed |
|---|---|
| `libwebkit2gtk-4.1-dev` | The browser engine Tauri uses to render the app's UI |
| `libgdk-pixbuf2.0-dev` | Image rendering support (dev headers, not just the runtime) |
| `libxdo-dev` | Window/keyboard automation library Tauri depends on |
| `libayatana-appindicator3-dev` | System tray support |
| `librsvg2-dev` | SVG rendering (used for app icons) |

**What success looks like:**

The command ends with something like:

```
Processing triggers for libc-bin (2.35-0ubuntu3.9) ...
```

No red `E:` error lines. If you see `E: Unable to locate package ...`, let me
know which package name and I'll help.

---

## Step 2 — Go to the app directory

The code lives here. Navigate to it:

```bash
cd /home/gregmorris/projects/depthfusion/.claude/worktrees/v2-lane-c-ui/app
```

Confirm you're in the right place:

```bash
pwd
```

You should see:

```
/home/gregmorris/projects/depthfusion/.claude/worktrees/v2-lane-c-ui/app
```

---

## Step 3 — Install JavaScript dependencies

The app's frontend is TypeScript/React. Before anything can compile,
you need to download its JavaScript packages. This is like `pip install`
but for JavaScript.

```bash
npm install
```

This downloads packages into a `node_modules/` folder. It takes 30–60 seconds.

**What success looks like:**

```
added 312 packages, and audited 313 packages in 45s
```

The exact number of packages doesn't matter — no `npm error` lines matter.

---

## Step 4 — Check the TypeScript compiles

This confirms the frontend JavaScript/TypeScript code has no errors:

```bash
npm run build
```

**What success looks like:**

```
✓ built in 1.23s
```

A `dist/` folder will be created. No errors printed in red.

---

## Step 5 — Check the Rust code compiles

This is the main check. Tauri's desktop shell is written in Rust.
This command checks the Rust code for errors without doing a full build
(which would take 5+ minutes). The first run downloads Rust crates
(packages) and takes about 2–3 minutes.

```bash
cargo check --manifest-path src-tauri/Cargo.toml
```

**What to expect while it runs:**

You'll see lines like:

```
Downloading crates ...
Compiling tauri v2.11.2
Compiling app v0.1.0
```

**What success looks like:**

The last line will be:

```
Finished `dev` profile [unoptimized + debuginfo] target(s) in 2m 14s
```

No `error[E...]` lines. Warnings (yellow `warning:` lines) are fine.

---

## What to do if Step 5 fails

### If you see: `pkg-config could not find: gdk-pixbuf-2.0`

Step 1 didn't complete properly. Run Step 1 again and look for any
`E:` error lines. Most likely the `apt update` didn't finish — try:

```bash
sudo apt update
sudo apt install -y libwebkit2gtk-4.1-dev libgdk-pixbuf2.0-dev
```

Then re-run Step 5.

### If you see: `pkg-config could not find: webkit2gtk-4.1`

The webkit package installed but `pkg-config` can't find it yet. Try:

```bash
sudo ldconfig
```

Then re-run Step 5.

### If you see: `error[E0...]: cannot find ...` (a Rust compile error in red)

This is a code error, not an environment error. Don't try to fix it yourself —
copy the full error output (everything between `error[` and `Finished`) and send it back.

---

## You're done

When Step 5 shows `Finished` with no errors, T-628 is verified.
The Tauri app compiles cleanly on this server. Report back with
the last 3 lines of output from Step 5.

---

## Quick reference — all commands in order

```bash
# Step 1 — install system libs
sudo apt update && sudo apt install -y \
  libwebkit2gtk-4.1-dev \
  libgdk-pixbuf2.0-dev \
  libxdo-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev

# Step 2 — go to the app directory
cd /home/gregmorris/projects/depthfusion/.claude/worktrees/v2-lane-c-ui/app

# Step 3 — install JS packages
npm install

# Step 4 — check TypeScript
npm run build

# Step 5 — check Rust
cargo check --manifest-path src-tauri/Cargo.toml
```
