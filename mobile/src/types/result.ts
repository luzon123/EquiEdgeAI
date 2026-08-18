/**
 * Shared result model — the ONLY shape the mobile client ever sees back
 * from the server. Mirrors routes/mobile.py's response exactly.
 *
 * The mobile app must never compute pot odds, equity, EV, or any poker
 * logic itself. If a field isn't in this type, the client has no business
 * knowing it.
 */

export type MobileAction = 'FOLD' | 'CHECK' | 'CALL' | 'BET' | 'RAISE' | 'ALL-IN';

export interface AnalyzeSuccess {
  winrate: number; // 0-100
  action: MobileAction;
}

export interface AnalyzeError {
  error: string; // short, human-readable — safe to render directly
}

export type AnalyzeResult = AnalyzeSuccess | AnalyzeError;

export function isAnalyzeError(result: AnalyzeResult): result is AnalyzeError {
  return (result as AnalyzeError).error !== undefined;
}
