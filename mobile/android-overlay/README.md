# Android Floating Overlay — Source of Record

**This has now been integrated into `mobile/android/`** (a real, RN-CLI-generated Android
project — see `mobile/android/README.md` for build status and exact commands). This
directory remains the source of record / reference copy: if you regenerate `mobile/android/`
from scratch, these are the exact files to re-merge, and this README documents *why* each
design choice was made, which the integrated copy's inline comments only partially repeat.

Written and reviewed carefully against current Android APIs (`MediaProjection`,
`WindowManager.TYPE_APPLICATION_OVERLAY`, foreground service types), but **has not been
compiled by an actual Gradle build in this environment** — no Android SDK/JDK here. See
`mobile/android/README.md` for exactly what's missing and what running `./gradlew` will
require.

## What's here

```
android-overlay/
  AndroidManifestAdditions.xml     — permissions + component registrations (already merged
                                      into mobile/android/app/src/main/AndroidManifest.xml)
  src/main/java/ai/equiedge/mobile/overlay/
    OverlayPermissionActivity.kt   — one-time setup: overlay permission + capture consent
    OverlayService.kt              — the floating button, drag handling, capture orchestration
    ScreenCapture.kt               — one-shot MediaProjection -> PNG bytes
    AnalyzeApi.kt                  — POST /mobile/analyze (same contract as iOS/RN)
    OverlayModule.kt               — RN native module: exposes startOverlaySetup() to JS
    OverlayPackage.kt              — ReactPackage registering OverlayModule
  src/main/res/...                 — button + result bubble layouts/drawables/strings
```

## What integrating this into a real project involves (already done in `mobile/android/`)

1. Copy `src/main/java/ai/equiedge/mobile/overlay/` into
   `android/app/src/main/java/ai/equiedge/mobile/overlay/`.
2. Copy `src/main/res/layout/`, `src/main/res/drawable/`, and merge
   `src/main/res/values/strings.xml`'s `overlay_button_label` string into the project's
   existing `strings.xml`.
3. Merge `AndroidManifestAdditions.xml` into `android/app/src/main/AndroidManifest.xml`
   (permissions inside `<manifest>`; `OverlayPermissionActivity` + `OverlayService` inside
   `<application>`).
4. Add to `android/app/build.gradle`'s `dependencies { ... }`:
   ```gradle
   implementation "androidx.core:core-ktx:1.12.0"
   implementation "androidx.activity:activity-ktx:1.8.2"
   implementation "androidx.appcompat:appcompat:1.6.1"
   ```
   (Declared explicitly for clarity even though `react-android`'s own AppCompat dependency
   likely already pulls these in transitively — direct declaration over relying on an
   unverified transitive graph.)
5. Set `minSdkVersion 26` in `android/build.gradle` — `WindowManager.TYPE_APPLICATION_OVERLAY`
   (the modern, non-deprecated overlay window type) requires API 26 (Android 8.0, 2017).
   That is the intentional floor; there is no fallback to the legacy `TYPE_PHONE` overlay
   type here (a different, effectively-deprecated permission model Google has been phasing
   out — not worth supporting for a 2026 target audience).
6. Enable `buildFeatures { buildConfig true }` and add a per-build-type
   `buildConfigField "String", "API_BASE_URL", "\"...\""` (debug -> dev server, release ->
   production) — `AnalyzeApi.kt` reads `BuildConfig.API_BASE_URL`, never a hardcoded string.
   See `mobile/android/app/build.gradle` for the exact values in use.
7. Register `OverlayPackage()` in `MainApplication.kt`'s `getPackages()` list, so RN's
   `NativeModules.OverlayModule` resolves. `mobile/src/native/overlayBridge.ts` wraps the
   call; `HomeScreen.tsx` has an Android-only "Enable Floating Overlay" button that invokes
   it — that's the reachable entry point into the flow below.

## Runtime flow

```
User opens the RN app -> taps "Enable Floating Overlay" (Android-only button, HomeScreen.tsx)
  -> OverlayModule.startOverlaySetup() -> OverlayPermissionActivity
     -> Settings.ACTION_MANAGE_OVERLAY_PERMISSION (system settings screen)
     -> MediaProjectionManager.createScreenCaptureIntent() (system consent dialog)
  -> OverlayService started with the granted projection token
     -> floating "ANALYZE" button appears, survives app backgrounding
User taps ANALYZE (or drags it to reposition first)
  -> button hides -> ~220ms settle delay -> one-shot screen capture
  -> button reappears
  -> PNG uploaded to POST /mobile/analyze on a background thread
  -> result bubble shows winrate + action for 4s (or until tapped)
```

## Why these specific design choices

- **One-shot capture, not continuous recording**: `ScreenCapture.captureOnce()` tears
  down its `VirtualDisplay`/`ImageReader` immediately after grabbing one frame. Running
  `MediaProjection` continuously between taps would burn battery for no benefit — the
  product only ever needs a snapshot at the moment the user taps ANALYZE.
- **`MediaProjection` requested once, reused across taps**: the system's screen-capture
  consent dialog is deliberately intrusive (it's a security-sensitive permission). Only
  `OverlayPermissionActivity` triggers it, once; `OverlayService` reuses that same
  `MediaProjection` object for every subsequent capture until it's explicitly stopped or
  the OS revokes it (`MediaProjection.Callback.onStop()`, handled).
- **Foreground service, `mediaProjection` type**: required by Android 10+ for any app
  capturing the screen in the background, and the specific `mediaProjection` foreground
  service *type* is required on API 29+ (and enforced more strictly from API 34). The
  service must call `startForeground()` with that type **before** requesting the
  `MediaProjection` instance — the ordering in `onStartCommand()` is load-bearing, not
  incidental.
- **Plain `HttpURLConnection`, not OkHttp/Retrofit**: keeps the overlay's dependency
  footprint to what's already explicitly declared in `mobile/android/app/build.gradle` —
  nothing silently assumed present.
- **`BuildConfig.API_BASE_URL`, not a hardcoded string**: standard Gradle mechanism (no new
  dependency) for a debug build to point at your dev machine while release points at
  production — see `mobile/android/app/build.gradle`'s `debug`/`release` build types.

## Known limitations / follow-ups

- **Not verified by an actual Gradle build** — no Android SDK/JDK in this environment. See
  `mobile/android/README.md` for the exact missing pieces and the commands to run once
  they're installed.
- **App icon placeholder**: `OverlayService`'s notification uses
  `android.R.drawable.ic_menu_view` (a system icon) as a placeholder — replace with a real
  app icon drawable before release; Android requires a valid small icon for the foreground
  notification.
- **OEM background restrictions**: some manufacturers (Xiaomi/MIUI, some Samsung/Huawei
  builds) impose extra "autostart"/battery-optimization restrictions beyond stock Android
  that can kill background services more aggressively than AOSP. There is no universal API
  fix for this — worth a device-specific FAQ/help screen once real users hit it.
- **Result bubble position**: anchored just below the button's last known position. If the
  button was dragged near the very bottom of the screen, the bubble could render partially
  off-screen — clamp its `y` similarly to the rotation-handling clamp in
  `onConfigurationChanged` if this shows up in testing.
