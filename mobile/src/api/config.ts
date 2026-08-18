/**
 * Server endpoint configuration.
 *
 * Points at the deployed backend (see render.yaml at the repo root for the
 * production deployment). No API keys or secrets live here or anywhere in
 * the mobile app — the Anthropic key stays server-side, never on-device.
 *
 * This is a plain constant, not process.env: bare React Native does not
 * wire up process.env without an extra native package (e.g.
 * react-native-config), and referencing it without one would silently
 * resolve to undefined at runtime rather than fail loudly. If per-build
 * environments (dev/staging/prod) are needed later, adopt
 * react-native-config and replace this constant with Config.API_BASE_URL —
 * don't reintroduce a bare process.env reference.
 */
export const API_BASE_URL: string = 'https://equiedge-ai.onrender.com';

export const ANALYZE_ENDPOINT = `${API_BASE_URL}/mobile/analyze`;

/** Fail fast rather than let the user stare at a spinner — a screenshot
 * analysis is a single request/response, not a long-running job. */
export const REQUEST_TIMEOUT_MS = 12_000;
