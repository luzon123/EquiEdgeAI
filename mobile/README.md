# EquiEdgeAI Mobile

Thin client for the existing poker decision engine (see repo root). This app captures a
screenshot, sends it to `POST /mobile/analyze`, and shows exactly two things: a winrate
and an action. It contains **no poker logic** — every equity/EV/decision calculation
happens server-side, in the same audited engine the web app uses.

## What's verified vs. what isn't (read this first)

This was built in a Windows sandbox with **no Xcode and no Android SDK/Gradle**. What that
means concretely:

| Piece | Status |
|---|---|
| `src/` (shared TS: API client, types, screens) | **Verified** — `npm install` succeeded, `npx tsc --noEmit` passes, and `npx react-native bundle` produced a real iOS JS bundle (Metro resolved every import, including `react-native-image-picker`). |
| `ios-share-extension/` (Swift) | **Not compiled.** Written against current public APIs, reviewed by hand. Needs Xcode. |
| `android-overlay/` (Kotlin) | **Not compiled.** Written against current AndroidX/platform APIs, reviewed by hand. Needs Android Studio / a JDK+SDK. |
| Backend `/mobile/analyze` | **Fully verified** — see repo-root test suite (`tests/test_mobile_api.py`, 19 tests) and `scripts/bench_mobile_api.py`. |

Don't take "written carefully" as "known to build." Open both native folders in their real
toolchains and fix whatever the compiler flags before shipping either one.

## Layout

```
mobile/
  App.tsx, index.js, app.json          — RN entry points
  src/
    api/config.ts, client.ts           — the ONE function that talks to the backend
    types/result.ts                    — {winrate, action} | {error} — nothing else
    screens/HomeScreen.tsx             — the entire production UI (one button, one result)
    theme/colors.ts
  ios-share-extension/                 — Swift source to add as an Xcode Share Extension target
  android-overlay/                     — Kotlin source to merge into the RN android/ project
  ios-screen-capture-research.md       — Phase 6: why Share Extension, not ScreenCaptureKit/ReplayKit
```

## Getting a runnable native project

This folder is the shared/business layer. To get an actual buildable app:

```bash
cd mobile
npm install
npx react-native init EquiEdgeAITemp --version 0.73.6   # in a scratch dir, to get ios/ and android/
# then copy the generated ios/ and android/ folders into this mobile/ directory
```

(A from-scratch `react-native init` generates the native Xcode/Gradle project scaffolding
that this environment has no toolchain to produce or verify — see the note above. Once you
have `ios/`/`android/` from a real init, `npm install` here already has the JS deps ready
to go.)

From there:
- `npm run ios` / `npm run android` — standard RN run commands, once `ios/`/`android/` exist
  and `pod install` has been run for iOS.
- Follow `ios-share-extension/README.md` to add the Share Extension target.
- Follow `android-overlay/README.md` to merge in the overlay service.

## Configuration

- **API base URL**: `src/api/config.ts` — a plain constant, currently pointed at the
  `render.yaml` production deployment. Bare RN doesn't wire up `process.env` without an
  extra native package (`react-native-config`); don't reintroduce a bare `process.env`
  reference without adding that dependency first (see the comment in `config.ts`).
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
