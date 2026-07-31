// Pure colour resolution for Kimi terminal tabs. Kept free of JupyterLab
// imports so it is directly executable under jest - the tint regressed twice
// (claude DEF-10, claude DEF-11) behind a green suite that never ran this
// logic.
import type { ISession } from './types';

// Kimi has no `/color` command, so a conversation's tab colour is DERIVED:
// a deterministic hash of its session id onto the six colour ids of
// jupyterlab_colourful_tab_extension (that extension owns the tab CSS and
// colour vocabulary; we only feed it the colour). Stable per conversation -
// the same session id always tints the same colour, on every reload.
export const KIMI_TAB_COLOUR_IDS: readonly string[] = [
  'rose',
  'peach',
  'lemon',
  'mint',
  'sky',
  'lavender'
];

/** Map a session id to one of the six tab colour ids. FNV-1a (32-bit) -
 * a simple stable string hash with good avalanche on short hex-ish ids;
 * Math.imul keeps the multiplication in 32-bit integer semantics. */
export function kimiTabColour(sessionId: string): string {
  let hash = 0x811c9dc5; // FNV offset basis
  for (let i = 0; i < sessionId.length; i++) {
    hash ^= sessionId.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193); // FNV prime
  }
  return KIMI_TAB_COLOUR_IDS[(hash >>> 0) % KIMI_TAB_COLOUR_IDS.length];
}

/** What a terminal is running, as resolved by the `terminal-cwd` probe. */
export interface ITerminalColourInfo {
  // Conversation the terminal runs, or null when it cannot be read.
  sessionId: string | null;
  cwds: string[];
}

/** A terminal takes its OWN conversation's colour (the hash of the session id
 * the probe read from its argv). Never resolve colour via the project row
 * first: a row carries only the representative conversation, so row-matching
 * would tint every terminal of a project with the representative's colour
 * (claude DEF-11). cwd fallback only when the conversation is unreadable -
 * longest path wins, so a nested project beats its parent, and the
 * representative row's own session id supplies the hash. Null clears. */
export function colourForTerminal(
  info: ITerminalColourInfo,
  sessions: ISession[]
): string | null {
  if (info.sessionId) {
    return kimiTabColour(info.sessionId);
  }
  let best: ISession | undefined;
  let bestLen = -1;
  for (const raw of info.cwds) {
    const cwd = raw.replace(/\/+$/, '');
    for (const s of sessions) {
      const p = s.project_path.replace(/\/+$/, '');
      if ((cwd === p || cwd.startsWith(p + '/')) && p.length > bestLen) {
        best = s;
        bestLen = p.length;
      }
    }
  }
  return best && best.session_id ? kimiTabColour(best.session_id) : null;
}
