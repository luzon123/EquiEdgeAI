# Android Floating Overlay — Setup

This directory is a **source drop**, not a standalone buildable module. It's meant to be
merged into the `android/` project that `react-native init` (or an existing RN build)
generates — the same reason `mobile/ios-share-extension/` isn't a standalone Xcode
project. This sandbox has no Android SDK / Gradle / `javac` installed, so none of this
has been compiled here — it has been written and reviewed carefully against the current
Android APIs it uses (`MediaProjection`, `WindowManager.TYPE_APPLICATION_OVERLAY`,
foreground service types), but **build it in Android Studio before shipping it.**

## What's here

```
android-overlay/
  AndroidManifestAdditions.xml     — permissions + component registrations to merge in
  src/main/java/ai/equiedge/mobile/overlay/
    OverlayPermissionActivity.kt   — one-time setup: overlay permission + capture consent
    OverlayService.kt              — the floating button, drag handling, capture orchestration
    ScreenCapture.kt               — one-shot MediaProjection -> PNG bytes
    AnalyzeApi.kt                  — POST /mobile/analyze (same contract as iOS/RN)
  src/main/res/...                 — button + result bubble layouts/drawables/strings
```

## Merge steps

1. Generate (or use the existing) `android/` project via `react-native init` /
   `npx react-native run-android` once, from `mobile/`.
2. Copy `src/main/java/ai/equiedge/mobile/overlay/` into
   `android/app/src/main/java/ai/equiedge/mobile/overlay/`.
3. Copy `src/main/res/layout/`, `src/main/res/drawable/`, `src/main/res/values/strings.xml`
   into the matching folders under `android/app/src/main/res/`.
4. Merge `AndroidManifestAdditions.xml` into `android/app/src/main/AndroidManifest.xml`
   (permissions inside `<manifest>`, the activity/service inside `<application>`).
5. Add to `android/app/build.gradle` `dependencies { ... }`:
   ```gradle
   implementation "androidx.core:core-ktx:1.12.0"
   implementation "androidx.activity:activity-ktx:1.8.2"
   implementation "androidx.appcompat:appcompat:1.6.1"
   ```
6. Set `minSdkVersion 26` in `android/build.gradle` — `WindowManager.TYPE_APPLICATION_OVERLAY`
   (the modern, non-deprecated overlay window type) requires API 26 (Android 8.0, 2017).
   That is the intentional floor; there is no fallback to the legacy `TYPE_PHONE` overlay
   type here (it requires a different, effectively-deprecated permission model Google has
   been phasing out — not worth supporting for a 2026 target audience).
7. Launch `OverlayPermissionActivity` from somewhere reachable in the RN app (a settings
   screen button, a native module call, or as the app's own launcher activity) to start
   the setup flow described below.

## Runtime flow

```
User opens app once
  -> OverlayPermissionActivity
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
- **Plain `HttpURLConnection`, not OkHttp/Retrofit**: this is a source drop into a host
  project of unknown dependency state — adding a networking library the host hasn't
  already declared would be presumptuous. Swap it for OkHttp freely once merged in if the
  host app already depends on it.

## Known limitations / follow-ups

- **Not verified by an actual Gradle build** — no Android SDK in this environment. Sanity
  checked by hand against current `androidx`/platform APIs; build it in Android Studio and
  fix whatever the compiler flags before shipping.
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
