# EquiEdgeAI Mobile

Thin client for the existing poker decision engine (see repo root). This app captures a
screenshot, sends it to `POST /mobile/analyze`, and shows exactly two things: a winrate
and an action. It contains **no poker logic** — every equity/EV/decision calculation
happens server-side, in the same audited engine the web app uses.

## What's verified vs. what isn't (read this first)

This was built in a Windows sandbox with **no Xcode and, at first, no Android SDK/JDK**.
What that means concretely:

| Piece | Status |
|---|---|
| `src/` (shared TS: API client, types, screens) | **Verified** — `npm install` succeeded, `npx tsc --noEmit` passes, and `npx react-native bundle` produced real iOS and Android JS bundles (Metro resolved every import, including `react-native-image-picker` and the native `OverlayModule` bridge). Re-verified after the RN 0.77.3 upgrade. |
| `android/` (real RN-CLI-generated project + integrated overlay, RN 0.77.3) | **Not compiled in this sandbox** — no JDK/Android SDK here (see `android/README.md`). Reviewed by hand against the real AndroidX/platform API surface; several real bugs were found and fixed this way (see `android/README.md`, including the 16 KB page-size fix — RN was upgraded 0.73.6 -> 0.77.3, the first version with 16 KB-aligned prebuilt native libraries). Run `android/scripts/verify-16kb.ps1` on a machine with the toolchain to get a real build+install+launch result. |
| `ios-share-extension/` (Swift) | **Not compiled.** Written against current public APIs, reviewed by hand. Needs Xcode (macOS-only). |
| Backend `/mobile/analyze` | **Fully verified** — see repo-root test suite (`tests/test_mobile_api.py`, 19 tests) and `scripts/bench_mobile_api.py`. |

Don't take "written carefully" as "known to build." `android/README.md` has the exact
missing-toolchain list and the commands to run once it's installed; open the iOS folder in
Xcode and fix whatever the compiler flags before shipping it.

## Layout

```
mobile/
  App.tsx, index.js, app.json          — RN entry points
  src/
    api/config.ts, client.ts           — the ONE function that talks to the backend
    types/result.ts                    — {winrate, action} | {error} — nothing else
    screens/HomeScreen.tsx             — the entire production UI (one button, one result)
    native/overlayBridge.ts            — JS wrapper around the Android OverlayModule
    theme/colors.ts
  android/                             — real RN-CLI-generated project, overlay integrated
                                          (see android/README.md for build status)
  android-overlay/                     — source of record for the overlay (already merged
                                          into android/ — see android-overlay/README.md)
  ios-share-extension/                 — Swift source to add as an Xcode Share Extension target
  ios-screen-capture-research.md       — Phase 6: why Share Extension, not ScreenCaptureKit/ReplayKit
```

## Getting a runnable app

**Android**: `android/` already exists and the overlay is already integrated — see
`android/README.md` for the exact missing-toolchain list and build/install commands.

**iOS**: no equivalent `ios/` project has been generated yet (this session's scope was
Android only). Generate one the same way `android/` was produced:

```bash
cd mobile
npx @react-native-community/cli@latest init EquiEdgeAITemp --version 0.77.3 --skip-install --pm npm --package-name ai.equiedge.mobile   # scratch dir
# copy the generated ios/ folder into this mobile/ directory
cd mobile && npm install && cd ios && pod install   # macOS + Xcode required
```

(Match the RN version used for `android/` — 0.77.3 — so both platforms stay on the same
release. `react-native init` is deprecated in favor of `@react-native-community/cli init`,
used above.)

Then follow `ios-share-extension/README.md` to add the Share Extension target.

## Configuration

- **API base URL**: `src/api/config.ts` — release builds point at the `render.yaml`
  production deployment; dev builds auto-derive the host from Metro's own bundle URL (the
  emulator's host alias or your machine's LAN IP, whichever served the JS), no manual IP
  editing needed. Bare RN doesn't wire up `process.env` without an extra native package
  (`react-native-config`); don't reintroduce a bare `process.env` reference without adding
  that dependency first (see the comment in `config.ts`). The Android floating overlay is a
  separate, JS-independent process and does **not** read this file — its base URL is
  `BuildConfig.API_BASE_URL` in `android/app/build.gradle` instead.
- **Bundle identifier / applicationId**: placeholder `ai.equiedge.mobile` throughout
  (iOS extension bundle ID, Android package name). Replace with your actual registered
  identifiers before submitting to either store.
- **No secrets anywhere in this app.** The `ANTHROPIC_API_KEY` lives only in the backend's
  environment (`.env` / Render dashboard) — never on-device, on any platform.

## Why no navigation library, no state management library, no Expo

- **One screen, two states (idle/result)** doesn't need `react-navigation`.
- **One async call, no shared/global state** doesn't need Redux/Zustand/etc.
- **Expo (managed workflow) cannot host a Share Extension or a
  `TYPE_APPLICATION_OVERLAY` foreground service** — both are bare-native-module
  territory. Since both are explicit requirements here, bare React Native is the only
  workflow that fits, not a preference.
