# iOS Screen-Capture Research (Phase 6)

Question: is there a faster-than-Share-Extension, publicly-supported iOS workflow for
"analyze the current poker table," and if so, what does it cost the user/architecture?

Evaluated: ScreenCaptureKit, ReplayKit, Share Extensions, Action Extensions, App Intents /
Shortcuts. Conclusion up front: **Share Extension stays the primary MVP flow.** There is
one genuinely faster follow-up worth building — an **App Intent over the Photos library**
— and one technically-possible-but-worse-UX alternative (ReplayKit broadcast) that isn't
recommended. No private or unsupported APIs are involved anywhere below.

## Why not ScreenCaptureKit

ScreenCaptureKit (introduced at WWDC 2022) lets a **macOS** app capture arbitrary on-screen
content from other applications — it is the modern replacement for CGWindowListCreateImage
on the Mac. It is **not** a general-purpose "capture any app's screen" API for third-party
iOS apps. iOS's sandboxing model does not grant one app raw pixel access to another app's
UI without the user explicitly starting a system-level recording session first (see
ReplayKit below) — that constraint is deliberate and Apple has not relaxed it for
third-party apps. There is no iOS entry point that lets EquiEdgeAI silently grab whatever
is currently on screen in ClubGG.

## Why not ReplayKit (with a caveat: it does technically work)

`RPSystemBroadcastPickerView` + a **Broadcast Upload Extension** is the one public,
App-Store-legal mechanism that *can* capture another app's screen content on iOS. The
tradeoffs make it a poor fit for this product shape:

- The user must **explicitly start a system screen recording** (tapping the broadcast
  picker button, or Control Center's Screen Recording toggle set to your extension) — this
  is a bigger ask than sharing one screenshot, not a smaller one.
- Once started, iOS shows a **persistent, unmissable "Recording" indicator** (red status
  bar / pill) for as long as the session runs, and the user must manually stop it. That's
  the right UX for "record a video," and the wrong UX for "check this one hand" — it reads
  as continuous surveillance of the table, not an on-demand lookup, and a visible recording
  indicator in a real-money poker client is exactly the kind of thing likely to draw
  unwanted attention (from the platform, from opponents watching a shared screen, etc.).
- It would require piping a live `CMSampleBuffer` stream out of the broadcast extension
  process into something that calls `/mobile/analyze` — solvable, but it's meaningfully
  more moving parts (App Group shared container, a running broadcast session to manage)
  for a workflow that's a strictly worse fit than either alternative below.

**Not recommended** for this product. Documented here so the option isn't silently
unconsidered, per the brief.

## The faster real option: an App Intent over the Photos library

iOS 16+'s **App Intents** framework lets an app expose a custom action to Shortcuts, Siri,
Spotlight, and (on hardware that has one) the Action Button / Back Tap. Shortcuts already
ships a **"Get Latest Screenshots"** action that reads recent screenshots straight from the
Photos library (no capture APIs involved — screenshots the user already took with the
standard side-button + volume-up gesture are just photos). Combining the two:

```
User: side button + volume up (native iOS screenshot gesture)
User: presses the Action Button (or Back Tap, or says "Hey Siri, analyze my hand")
  -> EquiEdgeAI's "Analyze Latest Screenshot" App Intent runs
     -> reads the most recent screenshot from Photos (PhotosKit, "Add Only" or
        "Recent Screenshots" limited access — no full-library permission needed)
     -> calls the SAME AnalyzeAPI.swift already shared with the Share Extension
     -> shows the result (a small SwiftUI snippet result view, or a system notification)
```

This is **faster than the Share Extension flow** — it removes the "open the share sheet,
find EquiEdgeAI" step entirely, replacing it with one hardware button press or one spoken
phrase. It costs:

- One-time Shortcut/Action-Button setup by the user (a few taps in Settings, once).
- Photos read permission (scoped to "recent screenshots," not the full library).
- A new App Intents extension target — architecturally, this is additive: it reuses
  `AnalyzeAPI.swift` verbatim (already factored out of `ShareViewController.swift` for
  exactly this reason) and needs only its own thin `AppIntent` conforming type plus Photos
  fetch code. Nothing in the Share Extension, the RN app, or the backend needs to change.

**Recommendation:** ship the Share Extension first (zero setup, discoverable, works the
moment it's installed). Add the App Intent as a fast-follow for users who want to shave the
interaction down further — it's genuinely faster, entirely public-API, and the codebase is
already structured so adding it doesn't touch anything else.

## Why not a plain Action Extension

Action Extensions (`NSExtensionPointIdentifier = com.apple.ui-services`) are the
predecessor pattern to Share Extensions for "act on this content" — for an image input,
they're functionally redundant with Share Extensions today and appear in a separate,
less-discoverable row of the share sheet. There's no capability gap here that would justify
using one instead of (or alongside) the Share Extension.

## Summary

| Mechanism | Can it capture arbitrary on-screen content? | User friction | Verdict |
|---|---|---|---|
| ScreenCaptureKit | No (macOS-only for third-party use) | — | Not usable on iOS |
| ReplayKit broadcast | Yes | High (persistent recording indicator, manual start/stop) | Not recommended |
| Share Extension | N/A — acts on an already-shared screenshot | Low (share sheet, one extra tap) | **Primary MVP (shipped)** |
| App Intent + Photos | N/A — acts on an already-taken screenshot | Lowest (one button/phrase, after one-time setup) | **Recommended fast-follow** |
