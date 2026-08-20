# EquiEdgeAI Android

## ANALYZE pipeline fix (current)

### Symptoms reported

APK built and launched fine (16 KB fix below still holds). Pressing the floating ANALYZE
button on a physical device: first press — button hides briefly, nothing reaches the
backend, button returns with no useful result; second press — a timeout-like error, then
the app crashes.

### Root causes (three, all confirmed by direct code inspection, not guessed)

1. **App crash on the second ANALYZE press — `MediaProjection.createVirtualDisplay()`
   called twice on the same instance.** The previous `ScreenCapture` created a fresh
   `VirtualDisplay`/`ImageReader` pair per tap and released it immediately after each
   capture, reusing the same `MediaProjection` object across taps. Per Android's own docs
   (see Sources): **"On Android 14 or higher, `createVirtualDisplay()` throws a
   `SecurityException`... [if you call] `createVirtualDisplay()` more than once on the same
   `MediaProjection` instance."** First tap: fine (first call). Second tap: second call on
   the same instance -> uncaught `SecurityException` -> crash. This matches "works/does
   nothing on first press, crashes on second" exactly.
2. **No request ever reaches the backend — cleartext (plain HTTP) traffic is blocked by
   default.** `targetSdkVersion` is 34 (>= 28), and neither the manifest nor a network
   security config declared a cleartext exception, so Android's OS-level network security
   policy silently blocks every `http://` connection `AnalyzeApi.kt` attempts — it never
   leaves the device. (The old README claimed this was already handled; it wasn't — no
   `app/src/debug/AndroidManifest.xml`/`network_security_config.xml` existed before this
   fix.)
3. **Wrong host even if cleartext were allowed — `10.0.2.2` is emulator-only.** The debug
   `API_BASE_URL` was hardcoded to the Android *emulator's* fixed host alias, which does not
   resolve to anything on a physical device.

### The fix

- **`mobile/android-overlay/.../ScreenCapture.kt`** (synced into `android/app/src/main/java/...`):
  rewritten so the `VirtualDisplay`/`ImageReader` pair is created exactly **once** per
  `MediaProjection` grant (in `init`) and reused for every subsequent tap — frames are
  drained continuously and cheaply discarded (`image.close()`, no decode) unless an ANALYZE
  request is actually pending, in which case that one frame is decoded and returned. This
  matches Google's documented pattern for repeated on-demand captures from one consent
  grant. `release()` tears the pipeline down exactly once, called from
  `OverlayService.onDestroy()` and from the `MediaProjection.Callback.onStop()` path.
- **`OverlayService.kt`**: replaced the bare `isAnalyzing` boolean with an explicit
  `AnalysisState { IDLE, CAPTURING, SENDING }` state machine — a tap is ignored unless
  `state == IDLE`, so concurrent/duplicate analyses can't happen, and every path (success,
  failure, capture timeout, permission revoked externally) resets to `IDLE`. Added
  `onCapturedContentResize` handling (resizes the persistent virtual display, e.g. on
  rotation) and made resource release explicit and idempotent (no more leaking the
  `ImageReader`/`VirtualDisplay` when the OS revokes the projection out from under the app).
- **`AnalyzeApi.kt`**: added a full request-lifecycle log trail (see below); no contract
  change.
- **`app/build.gradle`**: debug `API_BASE_URL` changed from `http://10.0.2.2:5000` to
  `http://127.0.0.1:5000`, paired with `adb reverse tcp:5000 tcp:5000` (see "Dev workflow"
  below) — the same USB-tunnel pattern this project already uses for Metro on port 8081, so
  no LAN IP/Wi-Fi dependency and no new backend change needed.
- **New `app/src/debug/res/xml/network_security_config.xml`** + **new
  `app/src/debug/AndroidManifest.xml`**: a debug-build-only cleartext exception scoped to
  `127.0.0.1` / `10.0.2.2` / `localhost` — not a blanket cleartext allowance. AGP merges
  `src/debug/` only into debug builds; the release manifest is untouched and stays
  HTTPS-only, so production security is unweakened.
- **`routes/mobile.py`** (backend, additive only — no behavior/response/status-code
  change, 86/86 existing tests still pass): added `[MOBILE]` log lines at each pipeline
  stage (request received, image received, image decoded, sending to Claude, Claude
  response received, returning result) so a failed request is traceable server-side too.
- **`app.py` — deliberately NOT changed.** Flask still binds `127.0.0.1:5000`. With
  `adb reverse`, the phone's connection to its own `127.0.0.1:5000` is tunneled by the local
  ADB server and reaches Flask as a connection *from* `127.0.0.1` on the PC — exactly what
  `host="127.0.0.1"` already accepts. Switching to `host="0.0.0.0"` + a LAN IP was
  considered and rejected as unnecessary for USB-connected physical-device development
  (larger change, Wi-Fi/firewall dependent, requires hand-typing a machine-specific IP the
  user was told not to have hardcoded for them).

### Diagnostic logging added (all tagged, filterable independently of the rest of logcat)

Android side — tag `EquiEdgeAnalyze`, in `OverlayService.kt`, `ScreenCapture.kt`,
`AnalyzeApi.kt`:
```
[ANALYZE] button clicked
[ANALYZE] state: IDLE -> CAPTURING
[ANALYZE] starting capture
[ANALYZE] waiting for next frame
[ANALYZE] image received, encoded NNNN bytes
[ANALYZE] bitmap created, NNNN bytes
[ANALYZE] state: CAPTURING -> SENDING
[ANALYZE] preparing HTTP request
[ANALYZE] API URL: http://127.0.0.1:5000/mobile/analyze
[ANALYZE] HTTP request started
[ANALYZE] HTTP response: 200 (NNNms)
[ANALYZE] response body received, NNN chars
[ANALYZE] parsing result
[ANALYZE] state: SENDING -> IDLE
[ANALYZE] result displayed: FOLD 11.4%
[ANALYZE][ERROR] <ExceptionClass>: <message>     # on any failure path
```
No secrets are logged (there are none in this flow — `/mobile/analyze` takes no API key
from the client).

Backend side — existing logger, `[MOBILE]` prefix, in `routes/mobile.py`:
```
[MOBILE] request received
[MOBILE] image received | bytes=NNNN
[MOBILE] image decoded | mime=image/png ms=N.N
[MOBILE] sending to Claude
[MOBILE] Claude response received | ms=N.N valid=True
[MOBILE] returning result | action=FOLD winrate=11.4
```

### Dev workflow — exact commands (run on your machine, in this order)

```powershell
# Terminal 1 — backend
cd C:\Users\Admin\Desktop\equiedgeai\EquiEdgeAI
python app.py
# Confirm: "Starting Poker Decision Engine on http://127.0.0.1:5000"

# Terminal 2 — Metro
cd mobile
npx react-native start --reset-cache
# If EADDRINUSE:8081 — Metro is already running from a previous session, that's fine, reuse it.

# Terminal 3 — tunnels + build + install
adb devices                          # confirm R5CY346XPJH ... device
adb reverse tcp:8081 tcp:8081        # Metro
adb reverse tcp:5000 tcp:5000        # Flask — THIS WAS MISSING BEFORE; 8081 alone never
                                      # forwards the backend port.
cd mobile\android
.\gradlew.bat clean assembleDebug --no-daemon
adb install -r app\build\outputs\apk\debug\app-debug.apk

# Terminal 4 — logcat, start BEFORE reproducing
adb logcat -c
adb logcat EquiEdgeAnalyze:D AndroidRuntime:E *:S
```

Then: open the app -> grant overlay permission if not already -> grant screen-capture
consent if not already -> open the poker table -> press ANALYZE -> watch Terminal 4 for the
`[ANALYZE]` trail and the backend terminal for the `[MOBILE]` trail -> press ANALYZE again
to confirm the second press now also completes without a crash.

If `adb reverse tcp:5000 tcp:5000` isn't viable (e.g. testing over Wi-Fi without USB
instead), see the "Testing against your local computer" section below for the LAN-IP
alternative and what else needs to change for it.

### What this fix does NOT change

Architecture, overlay/MediaProjection functionality, Android permissions, package name
(`ai.equiedge.mobile`), the 16 KB page-size fix below (still in place, unaffected — nothing
here touches native library packaging), or the backend's actual decision/vision/equity
logic (only additive logging).

### Sources

- [Android Developers: Media projection](https://developer.android.com/media/grow/media-projection) -
  "On Android 14 or higher, the createVirtualDisplay() method throws a SecurityException
  if... Calls createVirtualDisplay() more than once on the same MediaProjection instance" -
  and the recommended one-VirtualDisplay-per-consent-grant pattern used in the fix above.
- [Android Developers: Network security configuration](https://developer.android.com/training/articles/security-config) -
  per-build-variant cleartext exceptions scoped by domain (used instead of a blanket
  `usesCleartextTraffic="true"`).

### What was NOT verified in this sandbox (same limitation as the 16 KB fix below)

No JDK/Android SDK/adb/physical device here — confirmed again directly (`java -version`
shows only a bare JRE 1.8.0_401, no `javac`, no `ANDROID_HOME`). What **was** verified here:
the backend test suite (86/86 passing, including with the new `[MOBILE]` log lines active),
and manual line-by-line review of the Kotlin changes against the documented MediaProjection
API and Android network security config format. The build, install, launch, and the actual
ANALYZE end-to-end run **must be done on your machine** using the commands above — do not
treat this as confirmed fixed until you've run them and watched both log trails.

---

## 16 KB page-size fix

### Root cause

The APK built and ran, but Android reported it "not compatible with 16 KB pages" on a
physical device. **Root cause: the project was on React Native 0.73.6, whose prebuilt
native libraries (Hermes and react-android's core `.so` files, published to Maven by
Meta) were compiled without 16 KB ELF segment alignment.** This is not something fixable
by reconfiguring this project's own Gradle files alone — this project ships zero custom
native/C++ code of its own; every `.so` in the APK comes from precompiled AAR
dependencies, and their ELF alignment is fixed at whatever NDK Meta used when publishing
that AAR. Confirmed via Meta's own release notes and Google's official 16 KB migration
guide (see Sources below): **React Native 0.77 is the first release with full 16 KB page
size support** — 0.76 and earlier do not have it, no matter how the local Gradle/NDK/AGP
config is tuned.

`react-native-image-picker` (the only third-party native dependency in this project) was
checked and ships no native `.so`/`.cpp`/`CMakeLists` of its own (pure Kotlin/Swift calling
platform APIs) - it was not a contributor to this issue and did not need to change.

### The fix

Upgraded React Native 0.73.6 -> **0.77.3** (latest patch in the first fully-compliant
minor version - not jumping further, per "prefer the smallest, production-correct fix").
This is the actual fix, not a workaround: it replaces the prebuilt Hermes/react-android
`.so` files with versions Meta recompiled using a 16 KB-aligned NDK. Everything else below
follows from that version bump (AGP/NDK/Gradle are resolved transitively by the React
Native Gradle Plugin, which pins them per RN release) plus one explicit packaging setting.

**Not done:** no application architecture change, no removal of the floating overlay or
MediaProjection, no security/permission weakening, no framework switch. New Architecture
(Fabric/TurboModules) was left OFF (`newArchEnabled=false`), explicitly overriding RN
0.77's new default of `true`, specifically to keep the change surface limited to native
library alignment - this project's `OverlayModule`/`OverlayPackage` are legacy (non-Turbo)
native modules, a fully supported configuration that needs no rewrite.

### Versions before -> after

| Component | Before (0.73.6) | After (0.77.3) | Why it matters here |
|---|---|---|---|
| React Native | 0.73.6 | 0.77.3 | First RN version with 16 KB-aligned prebuilt Hermes/react-android `.so` |
| AGP | ~8.1.x (unpinned; resolved via RN Gradle Plugin) | **8.7.2** (confirmed from `@react-native/gradle-plugin@0.77.3`'s `gradle/libs.versions.toml`) | Google requires AGP >= 8.5.1 for correct 16 KB zip alignment of uncompressed native libs |
| Gradle | 8.3 | 8.10.2 | Pinned by the RN 0.77.3 template; compatible with AGP 8.7.2 |
| NDK | 25.1.8937393 (r25c) | 27.1.12297006 (r27) | Pinned by the RN 0.77.3 template; r27+ understands 16 KB alignment (r25 predates this work entirely) |
| compileSdk | 34 | 35 | RN 0.77.3 template default |
| targetSdk | 34 | 34 (unchanged) | Not required to change for this fix; left as-is to minimise deviation |
| buildToolsVersion | 34.0.0 | **35.0.0** | Google requires build-tools >= 35.0.0 for `zipalign` itself to support `-P 16` verification |
| minSdk | 21 (template) / 26 (this project) | 24 (template) / **26 (this project, re-applied)** | Unchanged - `TYPE_APPLICATION_OVERLAY` requirement, unrelated to 16 KB |
| Kotlin | 1.8.0 | 2.0.21 | RN 0.77.3 template default |
| Flipper | present (`com.facebook.react:flipper-integration`) | **removed** (dropped from RN's own template since ~0.75, replaced by React Native DevTools) | One less native-lib dependency to audit; not itself confirmed as a 16KB offender, but its removal is upstream RN's decision, not a workaround introduced here |
| `newArchEnabled` | `false` | `false` (explicitly re-set; template now defaults to `true`) | Keeps the change surface to native-lib alignment only |
| `packagingOptions.jniLibs.useLegacyPackaging` | not set | **`false`** (explicit) | The actual packaging setting that keeps native libs uncompressed + page-aligned in the APK zip; declared explicitly rather than left to an implicit AGP default |

### Verification: what was actually run here vs. what needs your machine

This sandboxed environment has no JDK/Android SDK/adb/physical device (re-confirmed at
the start of this fix - see conversation history). What was verified here:

- `npm install` succeeded against RN 0.77.3.
- `npx tsc --noEmit` - clean.
- `npx react-native bundle --platform android` - Metro 0.81.5 bundled successfully,
  resolving every import including the native `OverlayModule` bridge.
- `./gradlew assembleDebug --no-daemon` was run (not assumed) and reached exactly the
  same point as the pre-fix project - `settings.gradle` plugin resolution - before hitting
  the same pre-existing "No Java compiler found" wall (no JDK here). This confirms the
  Gradle *configuration* is well-formed up to that point; it does not confirm a successful
  compile, native-lib alignment, or on-device behaviour, since a JDK is required for all
  three.
- `scripts/verify-16kb.ps1`'s syntax was parse-checked with PowerShell's own
  `[System.Management.Automation.Language.Parser]::ParseFile` (zero errors) - the script
  itself was not executed end-to-end here, since that needs the SDK/NDK/device this
  sandbox doesn't have.

**Everything requiring a JDK, the Android SDK, or the physical device — the actual build,
native-lib inspection, ELF/zipalign verification, install, and launch — must be run on
your machine, where those exist.** Run:

```powershell
cd mobile\android\scripts
.\verify-16kb.ps1
```

It builds a fresh debug APK, extracts and lists every `.so`, checks ELF LOAD segment
alignment for each one against Google's documented `2**14`-or-higher requirement, verifies
APK zip alignment with `zipalign -P 16`, installs on the connected device, launches it, and
checks logcat for a crash-free start - printing PASS/FAIL for each. It also prints the
exact manual steps for the overlay-permission and MediaProjection-consent flows, which
require physically tapping through system dialogs and can't be scripted.

**Do not treat this fix as confirmed working until that script (or the equivalent manual
commands below) reports all of: BUILD SUCCESSFUL, every `.so` at `2**14` alignment or
higher, `zipalign -P 16` verification successful, install successful, and the app launching
without a crash — on your machine.**

### Manual verification commands (what the script above automates)

```powershell
# 1. Backend tests (unaffected by this change)
python -m unittest discover -s tests

# 2. Fresh debug build
cd mobile\android
.\gradlew.bat clean assembleDebug

# 3+5. Native libs + ELF alignment (repeat per .so; NDK path per the version above)
$ndk = "$env:ANDROID_HOME\ndk\27.1.12297006\toolchains\llvm\prebuilt\windows-x86_64\bin"
Expand-Archive -Path app\build\outputs\apk\debug\app-debug.apk -DestinationPath apk_out -Force
Get-ChildItem apk_out\lib -Recurse -Filter *.so
& "$ndk\llvm-objdump.exe" -p apk_out\lib\arm64-v8a\libhermes.so | Select-String LOAD
# Expect every LOAD line to show "align 2**14" or higher.

# 4. APK zip alignment (16 KB) - requires build-tools 35.0.0
& "$env:ANDROID_HOME\build-tools\35.0.0\zipalign.exe" -v -c -P 16 4 app\build\outputs\apk\debug\app-debug.apk

# 7-9. Install, launch, check for crash
adb install -r app\build\outputs\apk\debug\app-debug.apk
adb logcat -c
adb shell am start -n ai.equiedge.mobile/.MainActivity
adb logcat -d -t 300 | Select-String "FATAL EXCEPTION"   # expect no matches
```

### Sources

- [React Native 0.77 release notes](https://reactnative.dev/blog/2025/01/21/version-0.77) -
  "React Native 0.77 is the first version with full support" for Android 16 KB page size.
- [Android Developers: Support 16 KB page sizes](https://developer.android.com/guide/practices/page-sizes) -
  exact NDK/AGP/build-tools version requirements, `packagingOptions` configuration,
  `llvm-objdump`/`zipalign` verification commands used above.
- `@react-native/gradle-plugin@0.77.3`'s own `gradle/libs.versions.toml` (fetched via
  `npm pack` and inspected directly) - confirms AGP 8.7.2 is what RN 0.77.3 actually
  resolves, not assumed from documentation alone.

---

## Build status: previously FAILED here for a different reason (missing JDK), now unchanged

Prior to the 16 KB fix, `./gradlew assembleDebug` was attempted in this same sandboxed
environment and failed with `No Java compiler found, please ensure you are running Gradle
with a JDK` after successfully downloading the Gradle distribution - confirming no JDK is
installed here (only a bare JRE 1.8; `javac` is absent from PATH entirely) and no Android
SDK is configured. That gap is unrelated to the 16 KB issue and unchanged by this fix -
see "Verification" above for what was and wasn't possible to check here this time too.

## What's missing in this sandbox (checked directly, not assumed)

| Requirement | Needed | Found here |
|---|---|---|
| JDK | 17 | **Missing.** Only a bare JRE 1.8.0_401 (`javac` not on PATH at all). |
| Android SDK | `platform-35`, `build-tools;35.0.0`, `ndk;27.1.12297006`, `platform-tools` | **Missing.** No `ANDROID_HOME`/`ANDROID_SDK_ROOT`, no SDK at the standard Windows location. |
| adb / physical device | working adb + authorized device | **Missing here** (per your own report, present on your machine). |

## What to install (if not already present on your machine)

1. **JDK 17** - e.g. [Eclipse Temurin 17](https://adoptium.net/temurin/releases/?version=17).
   Verify with `javac -version` (not just `java -version`).
2. **Android SDK** components via Android Studio's SDK Manager or standalone
   `sdkmanager`:
   ```
   platforms;android-35
   build-tools;35.0.0
   ndk;27.1.12297006
   platform-tools
   ```
3. Environment variables:
   ```powershell
   $env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.x.x"
   $env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
   $env:Path += ";$env:ANDROID_HOME\platform-tools"
   ```

## Exact commands to build

```powershell
cd mobile
npm install
cd android
./gradlew assembleDebug
```

Output APK: `mobile/android/app/build/outputs/apk/debug/app-debug.apk`.

## Exact commands to install on a physical device

```powershell
adb devices                 # confirm one authorized device
cd mobile\android
./gradlew installDebug       # or: npx react-native run-android (also starts Metro)
```

## Testing against your local computer (not production)

Updated by the "ANALYZE pipeline fix" above — see that section for the full explanation.
Default (physical device over USB, current setup): `android/app/build.gradle`'s `debug`
build type points `BuildConfig.API_BASE_URL` at `http://127.0.0.1:5000`, and
`adb reverse tcp:5000 tcp:5000` tunnels that to Flask on the PC (Flask keeps binding
`127.0.0.1` — no backend change needed, the tunnel makes it look like a local connection).
Cleartext HTTP is allowed for debug builds only, scoped to `127.0.0.1`/`10.0.2.2`/`localhost`
via `android/app/src/debug/res/xml/network_security_config.xml` +
`android/app/src/debug/AndroidManifest.xml`; release still requires HTTPS and has no such
exception.

**Alternative — Android emulator:** change `API_BASE_URL` back to `http://10.0.2.2:5000`
(already covered by the same network security config) — no `adb reverse` needed, the
emulator's NAT resolves `10.0.2.2` on its own.

**Alternative — physical device over Wi-Fi, no USB tunnel:** find your computer's LAN IP
(`ipconfig` -> IPv4 Address), set `API_BASE_URL` to `http://<that-ip>:5000`, add that same
IP as a `<domain>` entry in `network_security_config.xml`, and change `app.py`'s
`application.run(host=...)` to `"0.0.0.0"` so Flask accepts connections from other devices
on the network (currently `127.0.0.1`, which only accepts same-machine/adb-reverse-tunneled
connections). The phone and PC must be on the same Wi-Fi network and the PC's firewall must
allow inbound connections on port 5000.

## Required Android permissions (declared in AndroidManifest.xml)

| Permission | Why |
|---|---|
| `INTERNET` | Upload screenshots to `/mobile/analyze` |
| `SYSTEM_ALERT_WINDOW` | Draw the floating overlay above other apps (ClubGG) |
| `FOREGROUND_SERVICE` | Run `OverlayService` as a foreground service (required Android 8+) |
| `FOREGROUND_SERVICE_MEDIA_PROJECTION` | Required for a foreground service that captures the screen (API 29+, stricter from API 34) |
| `POST_NOTIFICATIONS` | Show the foreground-service's required persistent notification (API 33+) |

`SYSTEM_ALERT_WINDOW` and MediaProjection consent are **not** manifest-granted - both
require explicit runtime user action (`OverlayPermissionActivity`; see
`mobile/android-overlay/README.md`).

## What was integrated here (across both sessions)

- Real `android/` project generated via the official RN CLI (now
  `@react-native-community/cli@latest init ... --version 0.77.3 --package-name ai.equiedge.mobile`),
  not hand-written.
- **Found and fixed a bug in the CLI's own package-name templating, twice** (reproduces
  identically at both 0.73.6 and 0.77.3): it produces `java/com/ai.equiedge.mobile/` (a
  literal dotted folder nested under a stray `com/`) instead of the required
  `java/ai/equiedge/mobile/` nested structure. Fixed by moving the files before anything
  else was added, both times.
- `mobile/android-overlay/`'s Kotlin source integrated into
  `app/src/main/java/ai/equiedge/mobile/overlay/`; resources into `app/src/main/res/`.
- **Found and fixed two real bugs during manual review** (no compiler available in this
  sandbox, so done by reading against the actual AndroidX/platform API surface):
  `OverlayService.kt` was missing an explicit `import ai.equiedge.mobile.R` (it lives in
  the `.overlay` sub-package, where the generated `R` class isn't implicitly visible), and
  `OverlayPermissionActivity.kt` called a method that doesn't exist
  (`ActivityCompat.startForegroundService` - the real method is on `ContextCompat`).
- `minSdkVersion` raised to 26 (`TYPE_APPLICATION_OVERLAY` requirement).
- `buildFeatures.buildConfig` + per-build-type `API_BASE_URL`.
- Native module `OverlayModule`/`OverlayPackage` so the overlay setup flow is reachable
  from the RN app (an Android-only "Enable Floating Overlay" button in `HomeScreen.tsx`)
  rather than a dangling, unreachable Activity.

None of the above has been confirmed by an actual successful compile, install, or launch
in this sandbox. Treat it as carefully reviewed and grounded in verified facts (version
numbers confirmed against real package metadata, not guessed), not build-verified, until
`scripts/verify-16kb.ps1` (or the manual commands above) succeeds on your machine.
