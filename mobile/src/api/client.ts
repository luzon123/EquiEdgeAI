/**
 * The ONE function that talks to the backend. Both the iOS share-sheet
 * flow and the Android overlay flow call this — neither platform shell
 * implements its own upload logic or its own poker logic.
 *
 * Single HTTP request per analysis, no polling, no WebSocket: the server
 * responds to a screenshot with one JSON object, which is exactly what a
 * plain request/response is for (see project mandate: no WebSockets
 * unless they provide a measurable benefit — they don't here).
 */
import { ANALYZE_ENDPOINT, REQUEST_TIMEOUT_MS } from './config';
import { AnalyzeResult, isAnalyzeError, MobileAction } from '../types/result';

/** A captured/shared screenshot, in the shape RN's fetch/FormData expects
 * for a multipart file part. Platform code produces this; this module
 * never knows or cares whether it came from a Share Extension hand-off or
 * a MediaProjection capture written to a temp file. */
export interface ImageSource {
  uri: string; // file:// URI (or content:// on Android)
  name: string; // e.g. "table.png"
  type: string; // e.g. "image/png" | "image/jpeg"
}

const GENERIC_ERROR = 'Analysis failed. Try again.';
const NETWORK_ERROR = 'Could not reach the server. Check your connection and try again.';
const TIMEOUT_ERROR = 'Analysis timed out. Try again.';

/**
 * Upload a screenshot and return the minimal decision result.
 *
 * Never throws for expected failure modes (network error, timeout,
 * non-2xx response, malformed response) — those all resolve to
 * {error: "<short message>"} so callers can render directly without a
 * try/catch. This function only rejects on truly unexpected bugs.
 */
export async function analyzeScreenshot(image: ImageSource): Promise<AnalyzeResult> {
  const form = new FormData();
  // React Native's FormData accepts this exact {uri, name, type} shape as
  // a file part — this is the RN-specific piece; everything else here is
  // plain fetch.
  form.append('image', ({ uri: image.uri, name: image.name, type: image.type } as unknown) as Blob);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(ANALYZE_ENDPOINT, {
      method: 'POST',
      body: form,
      signal: controller.signal,
      // No custom headers: RN's fetch sets the multipart boundary itself
      // from the FormData body. Overriding Content-Type here breaks the
      // boundary and the server will fail to parse the upload.
    });
  } catch (err: any) {
    clearTimeout(timer);
    if (err?.name === 'AbortError') {
      return { error: TIMEOUT_ERROR };
    }
    return { error: NETWORK_ERROR };
  }
  clearTimeout(timer);

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { error: GENERIC_ERROR };
  }

  if (!response.ok) {
    const message = (body as { error?: string } | null)?.error;
    return { error: typeof message === 'string' && message.length > 0 ? message : GENERIC_ERROR };
  }

  const winrate = (body as any)?.winrate;
  const action = (body as any)?.action as MobileAction | undefined;
  const VALID_ACTIONS: MobileAction[] = ['FOLD', 'CHECK', 'CALL', 'BET', 'RAISE', 'ALL-IN'];

  if (typeof winrate !== 'number' || !action || !VALID_ACTIONS.includes(action)) {
    return { error: GENERIC_ERROR };
  }

  return { winrate, action };
}

export { isAnalyzeError };
