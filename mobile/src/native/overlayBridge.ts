/**
 * Bridge to the native OverlayModule (android/app/src/main/java/ai/equiedge/mobile/overlay/OverlayModule.kt).
 *
 * Android-only: SYSTEM_ALERT_WINDOW + MediaProjection consent are native,
 * Activity-result-driven permission flows with no RN equivalent and no
 * iOS analog (the iOS entry point is the Share Extension instead — see
 * mobile/ios-share-extension/). Calling this on iOS is a no-op.
 */
import { NativeModules, Platform } from 'react-native';

export function isOverlaySupported(): boolean {
  return Platform.OS === 'android';
}

/** Launches OverlayPermissionActivity, which walks the user through overlay
 * + screen-capture permission and starts the floating ANALYZE button. */
export function startOverlaySetup(): void {
  if (!isOverlaySupported()) return;
  NativeModules.OverlayModule?.startOverlaySetup();
}
