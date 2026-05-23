# Debugging real-iPhone Safari from the Windows PC

**Issue:** [#17](https://github.com/ferraroroberto/voice-transcriber/issues/17)

The phone-validation pipeline — cache hygiene (#13), the WebKit/iPhone
Playwright projection and regression net (#16), the pre-ship gate
(`scripts/verify-before-ship.ps1`) — catches the large majority of iPhone
regressions on Windows before they ship. This document covers the residual
~15% that genuinely need a real iPhone, and makes that manual loop fast by
giving the PC **DevTools visibility** into the live phone.

This is reference material. There is no application code involved.

## When you need this

Reach for a real iPhone only when the pre-ship gate
(`pwsh -File scripts/verify-before-ship.ps1`) is **green** but the phone still
misbehaves. That combination means the bug is in the iOS *shell*, not the app
logic — exactly what Playwright's bundled WebKit on Windows cannot reproduce:

- **PWA-shell behaviour** — Add-to-Home-Screen container quirks, full-screen
  status-bar handling, `apple-mobile-web-app-capable` edge cases.
- **iOS keyboard** — real keyboard rendering and the accessory bar (the login
  password field, transcript editing).
- **Real WKWebView limits** — memory pressure over long takes, background-tab
  suspension policy (the exact territory of the #12 background-finalize and
  #14 resume work).
- **Microphone + media** — real `getUserMedia` permission prompts, the iOS
  recording indicator, `MediaRecorder` codec support (`audio/mp4` vs
  `audio/webm`), screen-lock mid-record.
- **Real Safari Trust Profile flows** (`/install-ca`), Wi-Fi ↔ cellular
  switches, Apple-silicon Safari rendering details the Playwright WebKit
  build doesn't ship.

If the bug also reproduces in the WebKit projection, it belongs in the
regression net (the issue #16 pattern), not here.

## A. DevTools against the live iPhone — `ios-webkit-debug-proxy`

[`google/ios-webkit-debug-proxy`](https://github.com/google/ios-webkit-debug-proxy)
(open source, MIT) bridges the iOS Web Inspector protocol to a local TCP port
that Edge/Chrome DevTools can attach to. It is Windows-supported and is the
only path to real-iOS-Safari DevTools without a Mac.

> **Versions move.** Documented against **`ios-webkit-debug-proxy` v1.9.2**
> (latest release as of 2026-05-21). If a newer release exists, pin and note
> it here when you next run this.

### Steps

1. **iPhone:** Settings → Safari → Advanced → enable **Web Inspector**.
2. **Connect:** plug the iPhone into the PC via USB. Tap **Trust** on the
   phone if prompted, and enter the passcode.
3. **Install the proxy on Windows** (any one of):
   - Chocolatey: `choco install ios-webkit-debug-proxy`
   - Scoop: `scoop install ios-webkit-debug-proxy`
   - Prebuilt binary: the release assets on the GitHub repo above.
   The proxy needs Apple's USB stack — that comes with **iTunes** or the
   standalone **Apple Devices** app. Install one if the proxy reports no
   devices.
4. **Run the proxy:**
   ```powershell
   ios_webkit_debug_proxy.exe -f chrome-devtools://devtools/bundled/inspector.html
   ```
   It prints a listener, by default on `:9221`, with one port per connected
   device.
5. **Attach DevTools:** open `http://127.0.0.1:9221` in Edge or Chrome on the
   PC. Pick the iPhone, then the Safari tab (or PWA — see below). Full
   DevTools — Console, Network, Sources/JS debugger — attaches to the live
   page on the phone.

### Debugging the installed PWA, not just Safari

The voice transcriber is normally used as a **Home-Screen PWA**, and the PWA
container is a *separate* WebKit process from Safari proper. In the proxy's
listing at `:9221` it shows up as its own entry, distinct from any open Safari
tabs — select that entry to debug the installed app. A bug that only
reproduces in the PWA (full-screen, no Safari chrome) and not in a normal
Safari tab is almost always a PWA-shell issue.

## B. Capturing evidence for a phone-only bug

When a phone-only bug turns up — reported or self-spotted — work the loop:

1. **Confirm the gate is green** on the failing change:
   `pwsh -File scripts/verify-before-ship.ps1`. A green gate is what tells you
   this is a shell issue and not app logic.
2. **Attach DevTools** to the live phone per Section A.
3. **Reproduce and capture:**
   - Console errors.
   - The Network waterfall.
   - The JS modules actually loaded — URL **and** the `?v=<hash>` content hash
     visible in the Network tab. A matching hash proves the phone is on the
     new build (the cache-busting from issue #13 worked); a stale hash means
     the cache bust is the bug, not the symptom you were chasing. Cross-check
     against the build line in the Settings panel and `/api/version`.
4. **If it only happens in the installed PWA**, debug the PWA target (Section
   A) — its container quirks won't show in a plain Safari tab.
5. **File it.** If the bug *does* reproduce in the WebKit projection but isn't
   yet handled, add an `xfail` test under `tests/e2e/` citing the new issue
   number (the issue #16 pattern). If it is genuinely iOS-shell-only and can't
   be reproduced under Playwright at all, record it as a documented
   manual-only check in the issue — don't fake an automated test for it.

## C. Cloud real-device alternative (reference only — no purchase needed)

For maintainers without a Mac or a spare iPhone, real-device clouds run the
test against actual hardware in a browser. Entry tiers are roughly
$30–$50/month.

| Service | Note |
|---|---|
| **BrowserStack Live** | Largest real-device pool; manual interactive sessions. |
| **Sauce Labs Real Device Cloud** | Strong automation/CI story; pricier. |
| **LambdaTest** | Cheapest entry tier; smaller device pool. |

**Do not** wire any of these into the codebase. This is reference material;
the voice transcriber ships no paid-service integration.

## D. Cloud Mac → iOS Simulator (reference only — no setup expected)

For completeness: [MacStadium](https://www.macstadium.com/) and
[MacInCloud](https://www.macincloud.com/) rent a macOS VM by the hour
(~$1/hour). A Mac VM runs Xcode's iOS Simulator, and the Simulator runs real
Mobile Safari with the full Safari **Develop** menu. It is the closest
substitute for a real iPhone short of buying a Mac — useful for an occasional
pre-release check, overkill for routine work.

## Summary

The automated pipeline (cache hygiene, the WebKit projection, the regression
net, the pre-ship gate) is the daily contract. This document is the fallback
for the rare iOS-shell bug it structurally cannot catch — and its whole point
is that `ios-webkit-debug-proxy` turns the "walk to the phone and guess" loop
into a normal DevTools session at the PC.
