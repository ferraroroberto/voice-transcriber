# iOS spin-off — system-wide voice keyboard

> **Status:** plan only, no code yet.
> **Goal:** an iPhone app + custom keyboard extension that gives you the same record → transcribe → polish flow as the webapp, but available **system-wide** by switching to a "Voice" keyboard inside any text field. The transcript is inserted natively at the caret of whatever app you're in (Mail, Messages, Safari, Notion, anywhere), without leaving that app.
> **Author audience:** Roberto + future-Claude. Written so a future agent can pick it up cold.

---

## TL;DR

You can't get this UX from the existing PWA — iOS PWAs cannot insert text into other apps. The only Apple-sanctioned way to deliver "speak in any app, transcript appears at the caret" is a **Custom Keyboard Extension**. That forces a native iOS project (Swift / SwiftUI, Xcode, a Mac, paid Apple Developer account).

The smart play is a thin native shell that **reuses the FastAPI backend you already have over the Cloudflare tunnel**. The phone never runs whisper or the LLM hub locally — it just records, uploads, and inserts the response. So 80% of the work you've already done (server, auth, history, polish, append, incognito, retention) carries over unchanged.

Three components ship together in one Xcode project:

1. **Container app** — full record/history/polish/settings UI, parity with the webapp.
2. **Keyboard extension** — minimal one-button "record → insert text at caret" UI, surfaced inside every app's keyboard switcher.
3. **Shared framework** — networking, auth, models, used by both.

**Difficulty (overall): 4 / 5** — not because the code is hard, but because shipping anything to iOS has a long checklist (Apple Dev account, certificates, provisioning profiles, App Group, entitlements, "Full Access" UX, mic privacy strings, memory budget for extensions). Most of this is one-time pain.

**Time estimate (calendar):** 2–4 weekends to a working personal build, +1–2 weekends for History/Polish parity. Side-loaded to your own phone, so no App Store review needed.

---

## What "auto-paste" actually means on iOS (important conceptual shift)

On the desktop, "auto-paste" means simulating Ctrl+V into the focused window. **iOS has no equivalent.** Apps are sandboxed and you cannot synthesise keystrokes into another app's input field.

The replacement, and the reason a custom keyboard is the only viable architecture: **`UITextDocumentProxy.insertText(_:)`**. When your custom keyboard is the active keyboard inside another app's text field, you can call this method and the text appears at the caret of that text field. It's not "paste" — it's better. No clipboard round-trip, no permissions popup per app, works in every text field iOS supports.

So:

- The user taps a text field in any app → switches keyboard to "Voice" via the globe key → taps record → speaks → taps stop → the transcript is inserted at the caret. No app switching at all.
- If the user wants the clipboard copy too (to paste somewhere else later), the keyboard can also write to `UIPasteboard.general.string`. But the primary mechanism is `insertText`.

**This is a fundamental architectural constraint.** Don't fight it. Don't try to make a Safari extension or a share-sheet target do this — they can't.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  iPhone                                                        │
│                                                                │
│  ┌────────────────────────┐       ┌─────────────────────────┐  │
│  │ Voice (container app)  │       │ Voice Keyboard          │  │
│  │ — full UI, parity with │       │ (UIInputViewController) │  │
│  │   webapp               │       │ — record + insertText   │  │
│  │ — login / token mgmt   │       │ — minimal UI, low mem   │  │
│  │ — settings persistence │       │ — RequestsOpenAccess ON │  │
│  └────────┬───────────────┘       └────────────┬────────────┘  │
│           │                                    │               │
│           └──────────────┬─────────────────────┘               │
│                          │                                     │
│                  ┌───────▼────────┐                            │
│                  │ Shared.framework│                           │
│                  │  - Networking  │   App Group:               │
│                  │  - AuthStore   │  group.com.ferraro.voice   │
│                  │  - Models      │  (UserDefaults + Keychain) │
│                  │  - History API │                            │
│                  └───────┬────────┘                            │
└──────────────────────────┼─────────────────────────────────────┘
                           │ HTTPS over Cloudflare tunnel
                           │ Bearer token + Cloudflare Access cookie
                           ▼
              ┌─────────────────────────────┐
              │  Home PC (already running)  │
              │  - tray.bat                 │
              │  - whisper-server :8090     │
              │  - FastAPI webapp :8443     │
              │  - cloudflared              │
              │  - local-llm-hub (polish)   │
              └─────────────────────────────┘
```

Key point: **the backend is unchanged** for v1. The iOS app talks to the same `/api/sessions`, `/api/sessions/{id}/upload`, `/api/sessions/{id}/polish`, `/api/sessions` (list), etc. that the webapp already uses. All the heavy lifting stays on the home PC.

---

## What you need before any code is written (your side)

These are blocking. None of them require coding skill, but they cost time and money and have to happen first.

| # | Item | Cost | Time | Notes |
|---|---|---|---|---|
| 1 | **Apple ID** | free | 5 min | You already have one (Apple account on your iPhone). |
| 2 | **Mac with Xcode 15+** | 0 if you own one, otherwise rent a cloud Mac (e.g. MacInCloud, ~$1/hr) or borrow one | — | Xcode is the only way to build/sign iOS apps. There is no way around this. iPad doesn't count. Linux/Windows can't sign for distribution. |
| 3 | **Apple Developer Program enrolment** | **$99/year** | ~24 h to approve | Required for: custom keyboard extensions, App Groups, persistent installs (>7 days), and TestFlight. Free Apple ID can side-load but extensions and app groups are flaky on free accounts and re-signing every 7 days is painful. **Pay the $99.** |
| 4 | **App Group registration** in Apple Developer portal | free with #3 | 5 min | Identifier like `group.com.ferraro.voice`. Both targets (app + keyboard) join it. |
| 5 | **Bundle identifiers** reserved | free with #3 | 5 min | e.g. `com.ferraro.voice` (app) + `com.ferraro.voice.keyboard` (extension). |
| 6 | **iPhone in Developer Mode** | free | 2 min | Settings → Privacy & Security → Developer Mode → on, then reboot. iOS 16+. |
| 7 | **Cloudflare tunnel reachable** | already done | — | The webapp is already exposed via `voice.<your-domain>` per `README.md`. Phone needs to reach this. |
| 8 | **Decide on auth** | — | — | Options: (a) bearer token only (simplest, paste tokenised URL into the app's settings once), (b) password gate that already exists in `scripts/set_password.py` (recommended — lets you type a 6-digit pin once on the phone). |

Once #1–#5 are done you have an **Xcode signing identity** that the project will use automatically. After that everything is just code.

---

## What I (Claude) need from you to start phase 1

When you are ready to actually start building, gather and tell me:

1. The Apple **Team ID** (10-char string from developer.apple.com → Membership).
2. The **bundle identifier** you want (default `com.ferraro.voice`).
3. The **App Group** name (default `group.com.ferraro.voice`).
4. The **Cloudflare URL** the app should default to (e.g. `https://voice.<your-domain>`).
5. Whether you want **token-only or password+token** auth on day one.
6. The **Mac path** where you want the Xcode project created (e.g. `~/Developer/voice-ios/`). Note: this lives **outside this Windows repo** because Xcode projects are macOS-native; we'll cross-link via README.

I will scaffold the Xcode project on the Mac and we'll iterate from there. The Xcode project ends up in its own git repo (or a sibling folder); this Windows repo only gains `docs/plans/ios-keyboard-app.md` (this file) and any backend changes phase 4 needs.

---

## Phased plan

Each phase is **stop-and-verify**. Don't start the next one until the previous one works on a real device.

### Phase 0 — Prep & decisions (no code)

**Difficulty:** 1/5 · **Time:** 1–3 days (mostly waiting on Apple to approve enrolment)

- Your side: items #1–#8 above. Most of the elapsed time is Apple's review of your Developer Program application.
- My side: nothing yet.
- **Verification:** you can sign into Xcode with your Apple ID and Xcode shows your team. You can create an empty SwiftUI "Hello World" app, run it on your iPhone, and see "Hello World" on the device.

**Learnings to expect:**
- iOS Developer Mode + first run takes longer than you think (notarisation, "Untrusted Developer" → Settings → VPN & Device Management → trust).
- The first time Xcode signs an app it may demand 2FA codes from your Apple ID.

---

### Phase 1 — Container app v0: parity-by-WKWebView

**Difficulty:** 2/5 · **Time:** 1 evening · **Goal:** ship the existing webapp inside a native shell so the iPhone has a real "Voice" app icon.

The CLAUDE.md memory says feature parity across surfaces matters. The webapp already nails the UX. The fastest way to get the container app to feature-parity is to **wrap the existing webapp in a `WKWebView`**. Zero UI rebuild, automatic parity forever.

**Components:**
- Single SwiftUI `App` with one `WebView` view.
- Wired to your Cloudflare URL.
- Reads/writes the bearer token in **Keychain** (shared via App Group so the keyboard extension can read it too).
- Handles the auth flow once natively (small SwiftUI form: "URL", "Password") on first launch, then loads the webapp with the bearer token injected as a header or in `localStorage`.

**My side:**
- Scaffold Xcode project with two targets: `Voice` (app) and `VoiceKeyboard` (keyboard extension, empty for now).
- Define App Group entitlement on both targets.
- `KeychainStore` helper in `Shared.framework`: read/write bearer token + base URL keyed by App Group.
- `WebView` SwiftUI wrapper with `WKWebView`, custom `URLSchemeHandler` or `WKHTTPCookieStore` injection so the bearer token rides on every request.
- Settings screen: base URL + password input → POST to `/api/login` → store token.

**Your side:**
- Plug iPhone into Mac, run the app from Xcode, accept the dev certificate prompt on the phone (Settings → VPN & Device Management → trust).
- Type your Cloudflare URL + password once.

**Verification:**
- The webapp loads, you can record from inside the container app, transcript comes back, polish works, history is visible.
- It looks and feels exactly like Safari's Home Screen icon — because it's basically the same `WKWebView`.

**Learnings:**
- `WKWebView` has its own cookie jar separate from Safari. Cloudflare Access cookies set in Safari won't carry over. The cleanest fix is **Cloudflare Access service tokens** or just use bearer-token + password (the password gate you already built), bypassing the Access prompt for the API path. Decide before this phase which auth model you want.
- Mic permission inside `WKWebView` requires `NSMicrophoneUsageDescription` in `Info.plist`. Set it.
- iOS PWA `localStorage` quirks (mentioned in `README.md` re: token storage) carry over verbatim into a `WKWebView` PWA. Same fix — read token from Keychain, inject via JavaScript bridge.

---

### Phase 2 — Keyboard extension v0: record → insert text

**Difficulty:** 4/5 · **Time:** 1–2 weekends · **The hard one.** Most novel concepts live here.

**Goal:** the user opens any text field, switches to the "Voice" keyboard, taps a big red button, speaks, taps stop, the transcript appears at the caret.

**Components:**
- `UIInputViewController` subclass — entry point of every iOS keyboard.
- A SwiftUI view hosted in the keyboard with: `[Voice ●]` record button, `[Stop ◼]`, status line, a `🌐` globe button to switch back to the system keyboard, error states.
- `AVAudioRecorder` capturing 16 kHz mono WAV (or m4a, transcoded server-side).
- `URLSession` upload to `/api/sessions/.../upload`. **Background upload session** so the recording survives even if the keyboard is dismissed mid-upload.
- On 200 OK with transcript: `textDocumentProxy.insertText(transcript)`.
- Reads bearer token + base URL from the shared **App Group** Keychain that the container app populated.

**Critical iOS keyboard constraints:**
1. **`RequestsOpenAccess = YES`** in `Info.plist`. Without it: no network, no mic, the keyboard runs in a sandbox so tight it can't even reach loopback. With it: full network access. **The user must enable it manually** in Settings → General → Keyboard → Keyboards → Voice → Allow Full Access. This is a one-time, scary-looking toggle. The container app should have a banner with a "How to enable" walkthrough until it detects open access is granted (the keyboard can write a "ping" to the App Group when it boots; the container reads it).
2. **Memory budget.** Keyboard extensions are killed by iOS when they exceed ~50–70 MB resident. This is why you stream upload (don't buffer the whole recording in RAM) and why the polish call should remain in the container app, not the keyboard. The keyboard does record + upload + insert; nothing else.
3. **No long-running tasks if dismissed.** When the user switches back to the system keyboard, your extension is suspended within ~5 s. Use `URLSession.shared` with `.background` configuration so the upload finishes even after suspension, and use `textDocumentProxy.insertText` from a background continuation that resumes when the keyboard becomes active again — or, simpler: **block the user from dismissing the keyboard until upload completes**, with a small spinner on the record button. Most takes are <2 s; this is fine.
4. **Microphone + privacy indicator.** While recording, iOS shows the orange dot in the status bar. Expected and good.
5. **Haptic feedback** — `UIImpactFeedbackGenerator` works in extensions and makes the record/stop transitions feel native.

**My side:**
- `KeyboardViewController.swift` — embeds SwiftUI via `UIHostingController`.
- `RecordingService` — wraps `AVAudioRecorder` with start/stop/levels.
- `UploadService` — multipart POST with bearer header, written to use `URLSession` `.background` config.
- `KeychainStore` already built in phase 1 — keyboard reads from it.
- A pre-flight check on each keyboard appearance: if no token in App Group Keychain, render "Open the Voice app and sign in first" with a button that opens the host app via `extensionContext?.open(URL)`.

**Your side:**
- Install via Xcode.
- Settings → General → Keyboard → Keyboards → Add New Keyboard → Voice → tap Voice → toggle **Allow Full Access**.
- Open Notes / Mail / Messages → tap a text field → tap globe → switch to Voice → record → confirm transcript appears.

**Verification:**
- Works in Notes, Mail, iMessage, Safari address bar, Notion, ChatGPT, Slack, Whatsapp.
- Works on lock-screen reply (this is a separate `RequestsOpenAccess` test — sometimes Apple restricts mic on lock screen).
- Network failure shows a clear error and doesn't insert garbage.
- Upload survives keyboard dismissal mid-upload.

**Learnings to expect (a.k.a. things that will bite):**
- The first time you flip "Allow Full Access" iOS will warn you that the keyboard "can transmit anything you type". Accept once.
- `AVAudioSession` configuration inside an extension is finicky: you'll likely need `.playAndRecord, mode: .measurement, options: [.defaultToSpeaker, .allowBluetooth]` and to call `setActive(true)` immediately before record / `false` immediately after, or you'll get audio routing weirdness with AirPods.
- `textDocumentProxy.insertText` does nothing if the proxy points at an unfocused field. It can also silently fail in some apps that override input handling (e.g. Google Docs in Safari occasionally). Document the known broken apps as you find them.
- Text fields with `secureTextEntry = true` (password fields) refuse third-party keyboards entirely. Expected; iOS falls back to the system keyboard automatically. **Do not try to record passwords** — both for the user's safety and because Apple will reject the app from review (not relevant for personal use, but a hint about why the OS does this).

---

### Phase 3 — Container app v1: native UI parity (optional but worth it)

**Difficulty:** 3/5 · **Time:** 1–2 weekends · **Goal:** replace the `WKWebView` inside the container app with native SwiftUI screens for record / history / polish / settings, matching the webapp 1:1.

This is optional. The `WKWebView` shell from Phase 1 already gets you full functionality. Native earns you:
- Native iOS gestures (swipe-to-delete on history rows, pull-to-refresh, share-sheet, drag-and-drop).
- Smaller binary, faster cold start.
- iOS-native dark mode, dynamic type, accessibility.

**Components:**
- `RecordView` — big record button, level meter, transcript box, polish button, append toggle, incognito toggle, language picker, polish-style picker. Same controls as `app/webapp/static/index.html`.
- `HistoryView` — list of sessions with checkbox selection, copy-selected, redo, delete, load-more pagination. Mirrors the webapp's `📜 History` panel.
- `SettingsView` — base URL, password / token, mic preferences, polish defaults, retention. Mirrors webapp `⚙️ Settings`.
- `APIClient` in `Shared.framework` — typed Swift wrappers around the FastAPI endpoints.

**My side:** straightforward SwiftUI work; no novel iOS APIs.

**Your side:** day-to-day testing.

**Verification:** every interaction in `app/webapp/static/index.html` has a 1:1 native equivalent. Use the existing `docs/2026-05-09-tk-webapp-parity.md` as a checklist.

---

### Phase 4 — Backend tweaks (small, but needed)

**Difficulty:** 1/5 · **Time:** half a day · **Goal:** make the existing FastAPI happy with iOS callers.

The webapp is already mobile-friendly, so most of this is hardening, not adding features.

- **CORS / origin checks.** A `WKWebView` and a native `URLSession` send different `Origin` headers (or none). Audit `app/webapp/server.py` for any check that breaks when `Origin` is missing. Likely none, but verify.
- **`/api/login`** already accepts a password and returns the bearer token. Confirm it returns JSON the iOS client can decode.
- **Long-running uploads.** Large takes (e.g. 5 min) over flaky cellular need either chunked upload (already supported) or a `Content-Length` honoured by uvicorn with reasonable timeouts. Bump `uvicorn` keep-alive and request timeouts if needed.
- **Tunnel keepalive.** Cellular networks aggressively kill idle TCP. Add or confirm Cloudflare's `keep_alive_connections` setting in `webapp/cloudflared.yml`.
- **Optional: device telemetry.** Add an optional `client` field to `/api/sessions` (e.g. `"ios-keyboard"`, `"ios-app"`, `"webapp"`, `"tk"`) so History can show which device dictated the take. Tiny addition, useful when you start using both surfaces interchangeably. Update `webapp_config.json` schema sample if you do.

**My side:** PR against this Windows repo with the small backend tweaks, in line with `CLAUDE.md` conventions.

**Your side:** restart the tray after pulling.

---

### Phase 5 — Polish, ship to your phone, daily-drive (no App Store)

**Difficulty:** 2/5 · **Time:** 1 weekend · **Goal:** durable install, no 7-day re-sign treadmill, ergonomic hotkeys.

Because this is for personal use, **no App Store submission is required**. With the paid Developer Program, side-loaded apps are valid for 1 year per signing cycle. Re-sign annually.

- **Distribution:** ad-hoc provisioning + Xcode → "Run" once a year.
- **TestFlight (alternative):** 90-day install, but limited to 100 internal testers and requires app review. Overkill for one user.
- **Icon + LaunchScreen:** reuse `app/webapp/static/icon-512*.png` (already in the repo) — Xcode wants 1024×1024 master + auto-generated tier sizes.
- **Globe key shortcut hint:** the keyboard's default appearance should include a 1-line "Long-press 🌐 to switch keyboards" hint until first use.
- **Crash reporting (optional):** Sentry or just Apple's built-in `~/Library/Logs/CrashReporter/MobileDevice/...`.

**Verification:** uninstall and reinstall once, confirm the Keychain still has the token via App Group (it will — Keychain survives app reinstall when the bundle ID is the same and the keychain access group is set correctly).

---

## Risks and gotchas (read these before starting)

1. **Apple Developer Program rejection / delays.** Roughly 1 in 20 enrolments hit a manual review (~1 week). Mitigation: enrol now, before you want to start.
2. **"Allow Full Access" UX is scary.** Users (you) get a stern warning that the keyboard can transmit everything typed. The container app should explain *why* that's needed (network for whisper, mic for recording) before sending you to Settings.
3. **Keyboard memory crashes.** If you ever load whisper.cpp into the keyboard for offline mode, it will get OOM-killed. **Don't.** Keep inference on the server.
4. **Cellular latency.** A 5-minute take uploads slowly on 4G. Show an honest progress bar. Most takes are <30 s; this is rarely an issue in practice.
5. **Cloudflare Access vs. native client.** Access expects a browser cookie flow. A native `URLSession` cannot do the OAuth dance. Two clean options:
   (a) **Service tokens** (Access feature) — issue a long-lived `CF-Access-Client-Id` + `CF-Access-Client-Secret` for the app. Works headlessly.
   (b) **Bypass Access for the API**, keep it for `/` browser access — Access lets you scope policies by path. Document either choice in `webapp_config.json`.
6. **iOS 18+ "Voice Isolation" / system audio processing.** When enabled it can interfere with mic capture in extensions. Workaround: explicit `AVAudioSession.Mode.measurement`.
7. **Background upload limits.** iOS gives extensions ~30 s of foreground time after dismissal. For longer uploads, register a `URLSessionConfiguration.background(withIdentifier:)` so the OS finishes the upload after the keyboard is gone. The transcript still arrives — just delivered to the **container app** via background callback, where you'd queue it for the next time the keyboard appears, or surface a notification "Transcript ready — open Voice to see it". For 99% of takes (<60 s) this never matters.
8. **Apple keyboard review (only if you ever submit).** If you go App-Store, custom keyboards face extra scrutiny: clear privacy policy, no ad-tech, and your "Allow Full Access" justification has to be airtight. Personal use side-load skips this entirely.

---

## Test plan

A condensed device-test checklist. Aim to pass each item before declaring a phase done.

**Phase 1 (container WebView):**
- [ ] App launches from Home Screen.
- [ ] First-launch settings screen accepts URL + password and persists.
- [ ] Webapp renders inside the WebView; record + transcribe works.
- [ ] Polish, history, append, incognito all behave identical to mobile Safari.
- [ ] Force-quit and relaunch — no re-login required.

**Phase 2 (keyboard):**
- [ ] Voice appears in Settings → Keyboards → Add New Keyboard.
- [ ] Allow Full Access toggle works; keyboard refuses to record without it and shows a clear instruction.
- [ ] In Notes: tap field → globe → Voice → record → "hello world" inserts at caret.
- [ ] Cursor placement respected: type "abc[caret]xyz", record "DEF", result is "abcDEFxyz".
- [ ] Append-mode behaviour preserved (or document that keyboard always inserts in-place; append-mode lives only in container).
- [ ] Network drop mid-record shows "Failed — tap to retry", no garbage inserted.
- [ ] Works on lock-screen reply to a notification.
- [ ] Works in Safari, Mail, Messages, WhatsApp, Slack, Notion, ChatGPT, Whatsapp.
- [ ] Memory monitor in Xcode stays under 50 MB during a 60 s record.

**Phase 3 (native UI):**
- [ ] Record screen feature-parity vs. webapp (use `docs/2026-05-09-tk-webapp-parity.md` as the reference matrix).
- [ ] History list paginates 10 at a time.
- [ ] Swipe-to-delete on history rows (native bonus).
- [ ] Settings persists and is shared with the keyboard extension (App Group write, keyboard reads).

**Phase 4 (backend):**
- [ ] `/api/login` happy path from a real iPhone over Cloudflare.
- [ ] CORS check: no preflight failures from the native client.
- [ ] 5-minute take over LTE completes without timeout.

**Phase 5 (release):**
- [ ] Reinstall preserves token (Keychain survival check).
- [ ] Cold launch < 1 s on a 13-mini.
- [ ] No analytics, no telemetry beacons leaving the device (Charles Proxy verification).

---

## File / repo layout impact

**This repo (Windows, voice-transcriber):**
- `docs/plans/ios-keyboard-app.md` — this file.
- Phase 4 backend tweaks (small) live in `app/webapp/server.py` + maybe `webapp/cloudflared.yml` notes.
- `README.md` gets a one-line pointer: "iOS keyboard app: see `docs/plans/ios-keyboard-app.md` and the sibling `voice-ios` repo."

**New repo (macOS, sibling, e.g. `voice-ios/`):**
```
voice-ios/
├── Voice.xcodeproj                  Xcode workspace
├── Voice/                           Container app (SwiftUI)
│   ├── VoiceApp.swift
│   ├── Screens/
│   │   ├── RecordView.swift
│   │   ├── HistoryView.swift
│   │   └── SettingsView.swift
│   └── Info.plist
├── VoiceKeyboard/                   Keyboard extension target
│   ├── KeyboardViewController.swift
│   ├── KeyboardView.swift           SwiftUI host
│   └── Info.plist                   RequestsOpenAccess = YES
├── Shared/                          Framework shared by both targets
│   ├── APIClient.swift
│   ├── KeychainStore.swift
│   ├── Models.swift
│   └── RecordingService.swift
├── Voice.entitlements               App Group + Keychain access
├── VoiceKeyboard.entitlements       Same App Group
└── README.md                        Points back at this plan
```

Why a separate repo: Xcode projects mix poorly with Windows checkouts (case-sensitivity, Mac binary plists, signing artefacts), and CLAUDE.md's conventions are Python/Streamlit-shaped — a Swift project deserves its own home. They reference each other; nothing is lost.

---

## Decision points — answer these before phase 1 starts

1. **Auth model:** token only, password+token (recommended), or Cloudflare Access service token?
2. **Container UI style:** WebView shell forever (Phase 1 is final), or migrate to native SwiftUI later (Phase 3)?
3. **Bundle identifiers:** stick with my defaults (`com.ferraro.voice` / `.keyboard`) or pick your own?
4. **One repo or two:** new sibling repo on the Mac (recommended) or attempt to keep it inside this Windows one?
5. **Optional features in v1:** translation toggle, polish styles dropdown, language picker — all on day one, or just record→insert and add the rest in a v1.1?

Once you answer these I can scaffold Phase 1 on your Mac in one session.

---

## Glossary (for future-you / future-Claude)

- **Container app**: the regular iOS app icon you tap from Home Screen. Required by Apple — a keyboard extension cannot ship standalone.
- **Keyboard extension** (a.k.a. *Custom Keyboard*): a `UIInputViewController` subclass loaded by iOS into other apps' text fields when the user picks it via the globe key.
- **App Group**: a shared sandbox container two targets in the same developer team can both read/write. We use it to share Keychain entries and `UserDefaults` between the app and the keyboard.
- **Allow Full Access**: a per-keyboard toggle in iOS Settings that grants network + mic + Keychain access to a third-party keyboard. Required for our use case. User-controlled.
- **`textDocumentProxy.insertText`**: the API that inserts a string at the caret of whatever text field is currently focused in whatever host app is currently in the foreground. The "auto-paste" replacement on iOS.
- **Side-loading**: installing a build directly from Xcode to a paired iPhone, bypassing the App Store. Free with a paid Developer Program for 1 year per signing cycle.
- **Service token (Cloudflare Access)**: a long-lived `CF-Access-Client-Id` + `Secret` pair issued by Cloudflare so non-browser clients can pass an Access policy without an OAuth flow.
