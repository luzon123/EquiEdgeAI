/**
 * Server endpoint configuration.
 *
 * No API keys or secrets live here or anywhere in the mobile app — the
 * Anthropic key stays server-side, never on-device.
 *
 * Release builds point at the deployed backend (see render.yaml at the
 * repo root). Dev builds auto-point at whichever machine served the
 * current JS bundle — the Android emulator's host alias, or a physical
 * device's view of your computer's LAN IP — with zero manual IP editing.
 *
 * Note: the Android floating overlay (mobile/android-overlay/) is a
 * separate, JS-independent process and does NOT read this file — its base
 * URL is configured via BuildConfig.API_BASE_URL in
 * android/app/build.gradle. This file only affects the in-app RN screen
 * (HomeScreen's manual picker flow) and, if built, an iOS equivalent.
 */
import { NativeModules, Platform } from 'react-native';

const PRODUCTION_URL = 'https://equiedge-ai.onrender.com';

// Matches app.py's default Flask dev port (`application.run(..., port=5000)`).
const DEV_BACKEND_PORT = 5000;

function extractHost(scriptURL: string): string | null {
  // Deliberately a regex, not `new URL(...)` — Hermes does not ship a WHATWG
  // URL implementation without an extra polyfill dependency, and this needs
  // nothing more than pulling the host out of "http://host:port/path".
  const match = /^https?:\/\/([^/:]+)(?::\d+)?/.exec(scriptURL);
  return match ? match[1] : null;
}

function deriveDevBaseUrl(): string {
  const scriptURL: string | undefined = NativeModules.SourceCode?.scriptURL;
  const host = scriptURL ? extractHost(scriptURL) : null;
  if (host) {
    return `http://${host}:${DEV_BACKEND_PORT}`;
  }
  // Fallback if scriptURL is missing/unparseable: correct for emulators;
  // a physical device would need the derivation above to have worked, or a
  // one-line hand-edit here.
  return Platform.OS === 'android'
    ? `http://10.0.2.2:${DEV_BACKEND_PORT}`
    : `http://localhost:${DEV_BACKEND_PORT}`;
}

export const API_BASE_URL: string = __DEV__ ? deriveDevBaseUrl() : PRODUCTION_URL;

export const ANALYZE_ENDPOINT = `${API_BASE_URL}/mobile/analyze`;

/** Fail fast rather than let the user stare at a spinner — a screenshot
 * analysis is a single request/response, not a long-running job. */
export const REQUEST_TIMEOUT_MS = 12_000;
