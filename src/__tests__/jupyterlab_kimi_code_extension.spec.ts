declare const __dirname: string;
declare function require(name: string): any;
// eslint-disable-next-line @typescript-eslint/no-var-requires
const fs: { readFileSync: (p: string, enc: string) => string } = require('fs');
// eslint-disable-next-line @typescript-eslint/no-var-requires
const path: { join: (...args: string[]) => string } = require('path');

import type { ISession } from '../types';
// Executed, not grepped - src/colour.ts is JupyterLab-free by design so the
// tint logic can actually be run under jest (the claude sibling's tint
// regressed twice behind a green suite that never ran it).
import {
  KIMI_TAB_COLOUR_IDS,
  colourForTerminal,
  kimiTabColour
} from '../colour';

const session = (over: Partial<ISession> = {}): ISession => ({
  project_path: '/p',
  encoded_path: '-p',
  session_id: 'session_sid',
  name: 'P',
  name_source: 'basename',
  message_count: 0,
  file_mtime: 0,
  git_branch: null,
  favourite: false,
  extra_sessions: 0,
  ...over
});

const widgetSrc: string = fs.readFileSync(
  path.join(__dirname, '..', 'widget.ts'),
  'utf-8'
);
const typesSrc: string = fs.readFileSync(
  path.join(__dirname, '..', 'types.ts'),
  'utf-8'
);
const indexSrc: string = fs.readFileSync(
  path.join(__dirname, '..', 'index.ts'),
  'utf-8'
);
const iconsSrc: string = fs.readFileSync(
  path.join(__dirname, '..', 'icons.ts'),
  'utf-8'
);
const css: string = fs.readFileSync(
  path.join(__dirname, '..', '..', 'style', 'base.css'),
  'utf-8'
);

/** Extract a method body from widget.ts. Non-greedy up to the first
 * class-level close (`\n  }`) - inner blocks close at deeper indents. */
const method = (re: RegExp): string => (widgetSrc.match(re) ?? [''])[0];

// A fixed, deterministic spread of realistic `session_<uuid>` ids.
const REALISTIC_IDS = Array.from(
  { length: 20 },
  (_, i) =>
    `session_${i.toString(16).padStart(8, '0')}-4b1d-4e2a-9c3f-${(i * 7)
      .toString(16)
      .padStart(12, '0')}`
);

/**
 * Kimi has no `/color` command, so the tab colour is DERIVED: a stable hash
 * of the session id onto the six colour ids owned by
 * jupyterlab_colourful_tab_extension. These EXECUTE the shipped hash.
 */
describe('kimiTabColour (executed)', () => {
  it('exports exactly the six colourful-tab ids', () => {
    expect(KIMI_TAB_COLOUR_IDS).toEqual([
      'rose',
      'peach',
      'lemon',
      'mint',
      'sky',
      'lavender'
    ]);
  });

  it('is deterministic - same input, same output, across calls', () => {
    for (const id of REALISTIC_IDS) {
      expect(kimiTabColour(id)).toBe(kimiTabColour(id));
    }
    const first = REALISTIC_IDS.map(id => kimiTabColour(id));
    const second = REALISTIC_IDS.map(id => kimiTabColour(id));
    expect(second).toEqual(first);
  });

  it('returns only ids from the exported six-id list', () => {
    for (const id of REALISTIC_IDS) {
      expect(KIMI_TAB_COLOUR_IDS).toContain(kimiTabColour(id));
    }
  });

  it('distributes across at least 3 distinct ids over realistic inputs', () => {
    const distinct = new Set(REALISTIC_IDS.map(id => kimiTabColour(id)));
    expect(distinct.size).toBeGreaterThanOrEqual(3);
  });

  it('handles a bare uuid and odd strings without throwing', () => {
    const odd = [
      '0f5c9a2e-4b1d-4e2a-9c3f-000000000000', // bare uuid, no prefix
      '',
      'not a uuid at all!',
      'path/with/slashes and spaces',
      'unicode ☃ input — dashes'
    ];
    for (const s of odd) {
      expect(() => kimiTabColour(s)).not.toThrow();
      expect(KIMI_TAB_COLOUR_IDS).toContain(kimiTabColour(s));
    }
  });
});

/**
 * Terminal tab tint resolution (claude DEF-11 equivalent). A terminal takes the
 * colour derived from its OWN running conversation; the project row is only
 * a cwd fallback for terminals whose conversation cannot be read.
 */
describe('colourForTerminal (executed)', () => {
  const rows = [
    session({ project_path: '/w', session_id: 'session_parent' }),
    session({ project_path: '/w/proj', session_id: 'session_nested' })
  ];

  it("the terminal's own session id wins over cwd matches (claude DEF-11)", () => {
    // The row representative for /w/proj is 'session_nested'; the terminal
    // runs 'session_bbb'. Its tint must hash from its OWN id, never the row.
    const got = colourForTerminal(
      { sessionId: 'session_bbb', cwds: ['/w/proj'] },
      rows
    );
    expect(got).toBe(kimiTabColour('session_bbb'));
    // Verified distinct hashes, so a row-resolved tint would differ here.
    expect(got).not.toBe(kimiTabColour('session_nested'));
  });

  it('null session id falls back to the longest-prefix cwd match', () => {
    expect(
      colourForTerminal({ sessionId: null, cwds: ['/w/proj'] }, rows)
    ).toBe(kimiTabColour('session_nested'));
    expect(
      colourForTerminal({ sessionId: null, cwds: ['/w/other'] }, rows)
    ).toBe(kimiTabColour('session_parent'));
  });

  it('nested project path beats its parent in the cwd fallback', () => {
    expect(
      colourForTerminal({ sessionId: null, cwds: ['/w/proj/deep'] }, rows)
    ).toBe(kimiTabColour('session_nested'));
  });

  it('no cwd match clears the tint', () => {
    expect(
      colourForTerminal({ sessionId: null, cwds: ['/elsewhere'] }, rows)
    ).toBeNull();
  });

  it('an empty session list clears the tint', () => {
    expect(
      colourForTerminal({ sessionId: null, cwds: ['/w/proj'] }, [])
    ).toBeNull();
  });
});

/**
 * Fork flow contract. Kimi forks server-side: POST sessions/fork copies the
 * session directory synchronously and returns the new id, then the launch
 * resumes that id. The claude-era argv fork (`fork_session_id` handed to the
 * CLI) and its `_watchForBranch` lazy-file poller must not survive the port.
 */
describe('fork (branch session) contract', () => {
  const branch = method(/private async _branchSession[\s\S]*?\n  \}/);

  it('asks for a name and posts it to sessions/fork', () => {
    expect(branch).toMatch(/InputDialog\.getText/);
    expect(branch).toMatch(/'sessions\/fork'/);
    expect(branch).toMatch(/name: title/);
    expect(branch).toMatch(/session_id: session\.session_id/);
  });

  it('calls sessions/fork first, then launch-terminal with the forked id', () => {
    const forkIdx = branch.indexOf("'sessions/fork'");
    const launchIdx = branch.indexOf("'launch-terminal'");
    expect(forkIdx).toBeGreaterThan(-1);
    expect(launchIdx).toBeGreaterThan(forkIdx);
    expect(branch).toMatch(/session_id: fork\.session_id/);
  });

  it('tags the launched terminal with the fork id for later reuse', () => {
    expect(branch).toMatch(/sessionId: fork\.session_id/);
  });

  it('has no fork_session_id argv flow left', () => {
    expect(widgetSrc).not.toMatch(/fork_session_id/);
  });

  it('has no _watchForBranch polling left (the fork is synchronous)', () => {
    expect(widgetSrc).not.toMatch(/_watchForBranch/);
    expect(widgetSrc).not.toMatch(/BRANCH_WATCH/);
    // One refresh after the fork suffices - the POST already wrote the copy.
    expect(branch).toMatch(/await this\._fetch\(\)/);
  });

  it('short session ids skip the constant session_ prefix (claude DEF-4 register)', () => {
    // Kimi ids are 'session_<uuid>': a front slice would render the same
    // 8 chars for every conversation. All display slicing goes through
    // _shortSessionId, which cuts from the uuid part.
    const short = method(/private _shortSessionId[\s\S]*?\n  \}/);
    expect(short).toMatch(/startsWith\('session_'\)/);
    expect(short).toMatch(/slice\(8, 16\)/);
    expect(widgetSrc).not.toMatch(/session_id\.slice\(0,\s*8\)/);
    expect(widgetSrc).not.toMatch(/current\.slice\(0,\s*8\)/);
  });

  it('caps the branch title inside menu items but not in the popup (DEF-18)', () => {
    // Lumino sets no max-width on `.lm-Menu-itemLabel`, and kimi auto-titles
    // a session from its first prompt, so an uncapped title stretched the
    // submenu to 850px - most of the window. Measured live before the fix.
    const menuLabel = method(/private _branchMenuLabel[\s\S]*?\n  \}/);
    expect(menuLabel).toMatch(/BRANCH_MENU_TITLE_MAX/);
    expect(menuLabel).toMatch(/slice\(0, BRANCH_MENU_TITLE_MAX\)/);
    expect(widgetSrc).toMatch(/const BRANCH_MENU_TITLE_MAX = \d+;/);
    // Only the title is trimmed: the short id and relative time are what
    // tell two branches apart, so they must survive the cap.
    expect(menuLabel).toMatch(/\$\{title\} \(\$\{shortId\}\)/);
    // Both Lumino submenus go through the capped variant...
    const menuUses = widgetSrc.match(/this\._branchMenuLabel\(b\)/g) ?? [];
    expect(menuUses).toHaveLength(2);
    // ...while the popup keeps the full string for its CSS ellipsis, and the
    // aria-label keeps it in full for screen readers.
    expect(widgetSrc).toMatch(
      /label\.textContent = this\._branchDisplayName\(b\)/
    );
    expect(widgetSrc).toMatch(/`Select \$\{this\._branchDisplayName\(b\)\}`/);
  });
});

/**
 * YOLO contract. Kimi's auto-approve flag is `--yolo`; every launch payload
 * carries the `yolo` key and the user-facing wording says YOLO, not the
 * claude-era "skip permissions" / "dangerous".
 */
describe('yolo contract', () => {
  // Launch bodies are typed consts (ILaunchTerminalRequest) declared at the
  // call sites, so the payload contract reads from those declarations.
  const launchPayloads: string[] = [];
  const re =
    /const (?:body|launchBody): ILaunchTerminalRequest = \{([\s\S]*?)\};/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(widgetSrc)) !== null) {
    launchPayloads.push(m[1]);
  }

  it('every launch-terminal payload uses the key `yolo`', () => {
    // resume, new session, fork - three launch sites.
    expect(launchPayloads).toHaveLength(3);
    for (const payload of launchPayloads) {
      expect(payload).toMatch(/yolo:/);
    }
  });

  it("context command 'resume-yolo' is labelled 'Resume (YOLO)'", () => {
    const cmd = method(
      /addCommand\('kimi-code-sessions:resume-yolo'[\s\S]*?\}\);/
    );
    expect(cmd).toMatch(/label: 'Resume \(YOLO\)'/);
    expect(cmd).toMatch(
      /this\._resumeInTerminal\(this\._activeSession, true\)/
    );
  });

  it('header menu offers New Kimi Session and New Kimi Session (YOLO)', () => {
    expect(widgetSrc).toMatch(/label: 'New Kimi Session',/);
    expect(widgetSrc).toMatch(/label: 'New Kimi Session \(YOLO\)'/);
    expect(widgetSrc).toMatch(
      /_newSessionMenu\.addItem\(\{\s*command: 'kimi-code-sessions:new-session'\s*\}\)/
    );
    expect(widgetSrc).toMatch(
      /_newSessionMenu\.addItem\(\{\s*command: 'kimi-code-sessions:new-session-yolo'\s*\}\)/
    );
  });
});

/**
 * Conversation-aware terminal reuse (claude DEF-4 equivalent). A terminal is
 * reused only on a POSITIVE session-id match from the terminal-cwd probe: an
 * unknown (null) running id never equals a wanted id, so a terminal whose
 * conversation cannot be read - or that runs a different conversation - is
 * never focused by mistake. There is no cwd-only reuse branch.
 */
describe('conversation-aware reuse gate (claude DEF-4 port)', () => {
  const findTerm = method(
    /private async _findTerminalForSession[\s\S]*?\n  \}/
  );
  const doResume = method(/private async _doResumeInTerminal[\s\S]*?\n  \}/);

  it('the probe response type carries the running session id', () => {
    const iface = (typesSrc.match(
      /export interface ITerminalCwdResponse \{[\s\S]*?\n\}/
    ) ?? [''])[0];
    expect(iface).toMatch(/session_id\?: string \| null/);
  });

  it('_findTerminalForSession keys on the session id alone', () => {
    // The wanted id is non-optional - ISession.session_id is required.
    expect(findTerm).toMatch(
      /_findTerminalForSession\(\s*wantedSessionId: string\s*\)/
    );
    // An empty wanted id short-circuits, so null never matches null.
    expect(findTerm).toMatch(
      /if \(!this\._terminalTracker \|\| !wantedSessionId\)/
    );
    // No cwd gate anywhere in the reuse walk.
    expect(findTerm).not.toMatch(/cwds/);
    expect(findTerm).not.toMatch(/=== target/);
  });

  it('the walk resolves each terminal via the shared probe', () => {
    expect(findTerm).toMatch(/this\._interrogateTerminal\(widget\)/);
    // One probe implementation: only _interrogateTerminal fetches the
    // terminal-cwd endpoint.
    expect(widgetSrc.match(/terminal-cwd\//g) ?? []).toHaveLength(1);
  });

  it('reuse requires positive id equality; null runningId never matches', () => {
    expect(findTerm).toMatch(/const runningId = info\?\.sessionId \?\? null/);
    expect(findTerm).toMatch(
      /if \(runningId === wantedSessionId\) \{\s*return \{ widget, runningId \};/
    );
    // No lenient/strict machinery survived the port.
    expect(findTerm).not.toMatch(/unknownConversation/);
    expect(findTerm).not.toMatch(/strict/);
  });

  it('the resume path resolves reuse through the id-gated probe walk', () => {
    expect(doResume).toMatch(/_findTerminalForSession\(session\.session_id\)/);
    expect(doResume).not.toMatch(/cwds/);
  });

  it('microcache reuse is gated purely on the conversation id', () => {
    expect(doResume).toMatch(/cached\.sessionId === session\.session_id/);
    expect(doResume).toMatch(
      /widget: found\.widget,\s*sessionId: found\.runningId \?\? undefined/
    );
  });

  it('a new session carries no pre-assigned id (kimi assigns it)', () => {
    const newSession = method(/private async _newSession[\s\S]*?\n  \}/);
    // The cache entry is untagged, and an unknown id is never reused.
    expect(newSession).toMatch(/sessionId: undefined/);
    expect(newSession).not.toMatch(/new_session_id/);
  });
});

/**
 * No-attach invariant. Kimi has no background agents: no attach verb in any
 * launch payload, no agent-owned rows, no bg chip. Every open names its
 * conversation and the server picks the argv.
 */
describe('no-attach / no-background-agent invariant', () => {
  it('no attach verb reaches any launch payload', () => {
    const re =
      /const (?:body|launchBody): ILaunchTerminalRequest = \{([\s\S]*?)\};/g;
    let m: RegExpExecArray | null;
    let count = 0;
    while ((m = re.exec(widgetSrc)) !== null) {
      count += 1;
      expect(m[1]).not.toMatch(/attach/i);
      expect(m[1]).not.toMatch(/bg_id/);
    }
    expect(count).toBe(3);
  });

  it('no background-agent state or labels remain in the widget', () => {
    expect(widgetSrc).not.toMatch(/attach_id/);
    expect(widgetSrc).not.toMatch(/Attach to Background Agent/);
    expect(widgetSrc).not.toMatch(/bg_id/);
  });

  it('the resume command label is a plain string, not an agent-aware fn', () => {
    const cmd = method(
      /addCommand\('kimi-code-sessions:resume', \{[\s\S]*?\}\);/
    );
    expect(cmd).toMatch(/label: 'Resume',/);
  });

  it('no bg chip rendering remains', () => {
    expect(widgetSrc).not.toMatch(/bgBadge/);
  });
});

/**
 * Launch-spinner dismiss contract. The spinner Dialog is constructed with
 * ``buttons: []`` so ``Dialog.resolve()`` has nothing to "click" and silently
 * no-ops - dismissal MUST be ``spinner.dispose()`` (the 1.1.13 -> 1.1.14
 * regression in the claude sibling).
 */
describe('launch spinner dismiss contract', () => {
  it('spinner is dismissed via dispose(), never resolve()', () => {
    expect(widgetSrc).toMatch(/spinner\.dispose\(\)/);
    expect(widgetSrc).not.toMatch(/spinner\.resolve\(\)/);
  });

  it('_showLaunchSpinner constructs the Dialog with buttons: []', () => {
    expect(widgetSrc).toMatch(/_showLaunchSpinner[\s\S]*?buttons:\s*\[\]/);
  });
});

/**
 * Manage Sessions popup contract: pinned current row, per-row Open and
 * copy-id, immediate delete via sessions/delete-branches, and a visible
 * selection counter.
 */
describe('Manage Sessions popup contract', () => {
  const popup = method(/private _showBranchPopup[\s\S]*?\n  \}/);

  it('is titled Manage Sessions', () => {
    expect(popup).toMatch(/title: 'Manage Sessions'/);
  });

  it('pins the current row first, badged and marked for assistive tech', () => {
    expect(popup).toMatch(/jp-mod-current/);
    expect(popup).toMatch(/branchCurrentBadge/);
    expect(popup).toMatch(/setAttribute\('aria-current', 'true'\)/);
    // The current row is appended before any checkbox row is built.
    const currentIdx = popup.indexOf('jp-mod-current');
    const checkboxIdx = popup.indexOf("check.type = 'checkbox'");
    expect(currentIdx).toBeGreaterThan(-1);
    expect(checkboxIdx).toBeGreaterThan(currentIdx);
    // And it is styled sticky with the brand accent in base.css.
    const cur = (css.match(
      /\.jp-KimiSessionsPanel-branchRow\.jp-mod-current \{[\s\S]*?\}/
    ) ?? [''])[0];
    expect(cur).toMatch(/position: sticky/);
    expect(cur).toMatch(/border-left: 3px solid var\(--jp-brand-color1\)/);
  });

  it('every row carries an Open button that launches that conversation', () => {
    expect(popup).toMatch(/jp-KimiSessionsPanel-branchOpen/);
    expect(popup).toMatch(/void this\._openBranch\(sessionId\)/);
    expect(popup).toMatch(/openButton\(current\)/);
    expect(popup).toMatch(/openButton\(b\.session_id\)/);
  });

  it('every row carries a copy-id button that copies without switching', () => {
    const helper = method(/private _branchCopyButton[\s\S]*?\n  \}/);
    expect(helper).toMatch(/stopPropagation/);
    expect(helper).toMatch(/Clipboard\.copyToSystem\(sessionId\)/);
    expect(popup).toMatch(/this\._branchCopyButton\(current\)/);
    expect(popup).toMatch(/this\._branchCopyButton\(b\.session_id\)/);
  });

  it('delete posts to sessions/delete-branches and resyncs the panel', () => {
    const del = method(/private async _deleteBranches[\s\S]*?\n  \}/);
    expect(del).toMatch(/'sessions\/delete-branches'/);
    // Self-caught: a throw inside `finally` overrides the try's `return`, so an
    // unguarded refresh failure rejects the whole call after a SUCCESSFUL
    // delete - swallowing the "N deleted" status and leaving the rows on screen.
    expect(del).toMatch(/finally[\s\S]*?await this\._fetch\(\)\.catch\(/);
    const delHandler = (popup.match(
      /deleteBtn\.addEventListener\('click'[\s\S]*?\n    \}\);/
    ) ?? [''])[0];
    expect(delHandler).toMatch(/this\._deleteBranches\(\[\.\.\.selected\]\)/);
  });

  it('shows a selection counter that tracks the ticked rows', () => {
    expect(popup).toMatch(/\$\{selected\.size\} selected/);
    expect(popup).toMatch(/Delete \(\$\{selected\.size\}\)/);
  });
});

/**
 * Cleanup-parallel popup contract: a confirm dialog naming the project and
 * the count precedes the POST, and a non-accept aborts it.
 */
describe('cleanup popup contract', () => {
  const cleanup = method(/private async _cleanupParallel[\s\S]*?\n  \}/);

  it('confirm dialog names the project and the parallel-session count', () => {
    expect(cleanup).toMatch(
      /showDialog\(\{\s*title: 'Clean Up Parallel Sessions'/
    );
    expect(cleanup).toMatch(/Remove \$\{extra\} parallel session/);
    expect(cleanup).toMatch(/"\$\{name\}"/);
    expect(cleanup).toMatch(/Dialog\.warnButton\(\{ label: 'Remove' \}\)/);
  });

  it('aborts when the dialog is not accepted, before any POST', () => {
    expect(cleanup).toMatch(/if \(!confirm\.button\.accept\) \{\s*return;/);
    const confirmIdx = cleanup.indexOf('confirm.button.accept');
    const postIdx = cleanup.indexOf("'sessions/cleanup'");
    expect(confirmIdx).toBeGreaterThan(-1);
    expect(postIdx).toBeGreaterThan(confirmIdx);
  });

  it('reports the removed count and refreshes on success', () => {
    expect(cleanup).toMatch(/Removed \$\{data\.removed_count\}/);
    const postIdx = cleanup.indexOf("'sessions/cleanup'");
    const refreshIdx = cleanup.indexOf('await this._fetch()');
    expect(postIdx).toBeGreaterThan(-1);
    expect(refreshIdx).toBeGreaterThan(postIdx);
  });

  it('self-catches the success refresh so a cleanup that worked is not reported as failed', () => {
    // The refresh sits inside the same try as the POST. Unguarded, a failed
    // GET sessions falls into the catch and overwrites "Removed N parallel
    // sessions." with "Cleanup failed" - for an operation that did remove them.
    expect(cleanup).toMatch(/await this\._fetch\(\)\.catch\(/);
  });
});

/**
 * Refresh and polling contract: the list is re-read bypassing the browser
 * cache, the background poll runs on a 30 s interval, and persisted UI state
 * is namespaced under the extension's own key prefix.
 */
describe('refresh and polling contract', () => {
  it("fetches GET sessions with cache 'no-store'", () => {
    const fetchBody = method(/private async _fetch\(\)[\s\S]*?\n  \}/);
    expect(fetchBody).toMatch(
      /requestAPI<ISessionsListResponse>\(\s*'sessions',\s*this\._serverSettings,\s*\{ cache: 'no-store' \}/
    );
  });

  it('polls on a 30 s constant', () => {
    expect(widgetSrc).toMatch(/const POLL_INTERVAL_MS = 30_000/);
    const poll = method(/private _startPolling[\s\S]*?\n  \}/);
    expect(poll).toMatch(/POLL_INTERVAL_MS/);
  });

  it("localStorage keys are namespaced 'jupyterlab_kimi_code_extension:'", () => {
    expect(widgetSrc).toMatch(
      /EXPANDED_STORAGE_KEY =\s*'jupyterlab_kimi_code_extension:expanded'/
    );
    expect(widgetSrc).toMatch(/getItem\(EXPANDED_STORAGE_KEY\)/);
    expect(widgetSrc).toMatch(/setItem\(EXPANDED_STORAGE_KEY,/);
  });
});

describe('activation contract (index.ts)', () => {
  it('logs exactly the template activation message', () => {
    expect(indexSrc).toMatch(
      /console\.log\(\s*'JupyterLab extension jupyterlab_kimi_code_extension is activated!'\s*\)/
    );
  });
});

/**
 * Types contract: the kimi session row carries no claude-era fields
 * (remote_control, color, bg_id), and the fork endpoint types exist.
 */
describe('types contract (types.ts)', () => {
  const iSession = (typesSrc.match(
    /export interface ISession \{[\s\S]*?\n\}/
  ) ?? [''])[0];

  it('ISession exists and has no remote_control / color / bg_id fields', () => {
    expect(iSession).toMatch(/session_id: string;/);
    expect(iSession).not.toMatch(/remote_control/);
    expect(iSession).not.toMatch(/color/);
    expect(iSession).not.toMatch(/bg_id/);
  });

  it('ISession carries no dead fields (zero readers server- or client-side)', () => {
    expect(iSession).not.toMatch(/summary/);
    expect(iSession).not.toMatch(/first_prompt/);
    expect(iSession).not.toMatch(/created/);
    expect(iSession).not.toMatch(/modified/);
  });

  it('POST bodies at the call sites are typed by the request interfaces', () => {
    expect(widgetSrc).toMatch(/const body: IFavouriteRequest = \{/);
    expect(widgetSrc).toMatch(/const body: IRemoveRequest = \{/);
    expect(widgetSrc).toMatch(/const cleanupBody: ICleanupRequest = \{/);
    expect(widgetSrc).toMatch(/const body: ISwitchRequest = \{/);
    expect(widgetSrc).toMatch(/const body: IDeleteBranchesRequest = \{/);
    expect(widgetSrc).toMatch(/const forkBody: IForkRequest = \{/);
    expect(widgetSrc).toMatch(
      /const (?:body|launchBody): ILaunchTerminalRequest = \{/
    );
  });

  it('declares IForkRequest and IForkResponse for the server-side fork', () => {
    const req = (typesSrc.match(
      /export interface IForkRequest \{[\s\S]*?\n\}/
    ) ?? [''])[0];
    expect(req).toMatch(/encoded_path: string;/);
    expect(req).toMatch(/session_id: string;/);
    expect(req).toMatch(/name\?: string;/);
    const res = (typesSrc.match(
      /export interface IForkResponse \{[\s\S]*?\n\}/
    ) ?? [''])[0];
    expect(res).toMatch(/session_id: string;/);
    expect(res).toMatch(/forked_from: string;/);
  });
});

/**
 * Stylesheet contract: the panel is styled under jp-KimiSessionsPanel, and
 * the claude-era remote-control dot and background-agent chip rules did not
 * survive the port.
 */
describe('stylesheet contract (base.css)', () => {
  it('styles the panel under jp-KimiSessionsPanel classes', () => {
    expect(css).toMatch(/\.jp-KimiSessionsPanel \{/);
    expect(css).toMatch(/\.jp-KimiSessionsPanel-header \{/);
    expect(css).toMatch(/\.jp-KimiSessionsPanel-row \{/);
    expect(css).toMatch(/\.jp-KimiSessionsPanel-branchPopup \{/);
    expect(css).toMatch(/\.jp-KimiSessionsPanel-branchOpen \{/);
    expect(css).toMatch(/\.jp-KimiSessionsPanel-branchDelete \{/);
    expect(css).toMatch(/\.jp-KimiSessionsPanel-loading:not\(\[hidden\]\) \{/);
  });

  it('keeps no claude-era selectors', () => {
    expect(css).not.toMatch(/ClaudeSessionsPanel/);
  });

  it('keeps no remote-control dot rule (the placeholder spacer is fine)', () => {
    expect(css).not.toMatch(/\.jp-KimiSessionsPanel-dot \{/);
  });

  it('keeps no bg-badge chip rule', () => {
    expect(css).not.toMatch(/bgBadge/);
  });
});

describe('icons contract (icons.ts)', () => {
  it('registers the kimi icon under the extension namespace', () => {
    expect(iconsSrc).toMatch(/name: 'jupyterlab_kimi_code_extension:kimi'/);
  });
});

/**
 * Removal spinner contract: the row-removal spinner must stack the lowercase
 * base class (which owns the spin animation) with the PascalCase override
 * (which only sizes it) - without the base class it renders invisible.
 */
describe('removal spinner contract', () => {
  it('the render site stacks the animated base class with the override', () => {
    expect(widgetSrc).toMatch(
      /'jp-kimi-sessions-panel-spinner jp-KimiSessionsPanel-spinner'/
    );
  });

  it('base.css sizes the override to the 8px placeholder slot', () => {
    const rule = (css.match(/\.jp-KimiSessionsPanel-spinner \{[\s\S]*?\}/) ?? [
      ''
    ])[0];
    expect(rule).toMatch(/width: 8px/);
    expect(rule).toMatch(/height: 8px/);
  });
});

/**
 * Panel accessibility and poll behaviour. These pin the round-1/round-2 UI
 * fixes that otherwise leave no trace in the suite: keyboard-operable rows,
 * a focus ring that survives the list's scroll clip, focus surviving the 30 s
 * poll's full rebuild, reduced motion, the time column, the out-of-root menu
 * gates, and a deferred (not dropped) poll tick.
 */
describe('panel accessibility and poll contracts', () => {
  const render = method(/private _render\(\): void \{[\s\S]*?\n  \}/);
  const renderSection = method(/private _renderSection[\s\S]*?\n  \}/);
  const renderRow = method(/private _renderRow\([\s\S]*?\n  \}/);

  it('rows are keyboard-operable (tabIndex, role, keydown)', () => {
    expect(renderRow).toMatch(/row\.tabIndex = 0/);
    expect(renderRow).toMatch(/setAttribute\('role', 'button'\)/);
    expect(renderRow).toMatch(/addEventListener\('keydown'/);
    expect(renderRow).toMatch(/e\.key === 'Enter' \|\| e\.key === ' '/);
  });

  it('the section caret is decorative - hidden from assistive tech', () => {
    expect(renderSection).toMatch(
      /caret[\s\S]*?setAttribute\('aria-hidden', 'true'\)/
    );
  });

  it('focusable rows get a ring that the list scroll container cannot clip', () => {
    const rule = (css.match(
      /\.jp-KimiSessionsPanel-row:focus-visible \{[\s\S]*?\}/
    ) ?? [''])[0];
    expect(rule).toMatch(/outline: 2px solid var\(--jp-brand-color1\)/);
    expect(rule).toMatch(/outline-offset: -2px/);
  });

  it('_render restores row focus across the rebuild, as it does scrollTop', () => {
    // Each row carries a stable identity for the restore to match on.
    expect(renderRow).toMatch(/row\.dataset\.rowKey = \[/);
    expect(render).toMatch(/document\.activeElement/);
    expect(render).toMatch(/dataset\s*\.rowKey/);
    expect(render).toMatch(/focus\(\{ preventScroll: true \}\)/);
  });

  it('focus restore covers section headers, not only rows', () => {
    // The header is a <button> inside _bodyEl whose own click handler calls
    // _render(), so collapsing a section destroyed its own focus on 100% of
    // toggles - deterministic, unlike the poll-tick case. Matching on
    // [data-row-key] rather than the row class covers both focusables.
    expect(renderSection).toMatch(/header\.dataset\.rowKey = key/);
    expect(render).toMatch(/closest<HTMLElement>\('\[data-row-key\]'\)/);
    expect(render).toMatch(
      /querySelectorAll<HTMLElement>\('\[data-row-key\]'\)/
    );
  });

  it('reduced motion reaches the spinners AND the indeterminate progress', () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
    expect(css).toMatch(/jp-kimi-sessions-panel-pulse/);
    // A UA-animated indeterminate <progress> cannot be stopped from author
    // CSS, so the cleanup bar is made determinate at its creation site.
    const cleanup = method(/private async _cleanupParallel[\s\S]*?\n  \}/);
    expect(cleanup).toMatch(
      /matchMedia\('\(prefers-reduced-motion: reduce\)'\)\.matches/
    );
    expect(cleanup).toMatch(/bar\.value = 0/);
  });

  it('the row time column aligns on digit stems like its popup sibling', () => {
    const rule = (css.match(/\.jp-KimiSessionsPanel-rowTime \{[\s\S]*?\}/) ?? [
      ''
    ])[0];
    expect(rule).toMatch(/min-width: 4em/);
    expect(rule).toMatch(/font-variant-numeric: tabular-nums/);
  });

  it('out-of-root projects grey out the terminal / file-browser items', () => {
    const gate =
      /isEnabled: \(\) =>[\s\S]*?_pathUnderRoot\(this\._activeSession\.project_path\) !== null/;
    const term = method(
      /addCommand\('kimi-code-sessions:open-terminal'[\s\S]*?\n    \}\);/
    );
    const fb = method(
      /addCommand\('kimi-code-sessions:show-in-filebrowser'[\s\S]*?\n    \}\);/
    );
    expect(term).toMatch(gate);
    expect(fb).toMatch(gate);
    // The gate is the only surface - Lumino never executes a disabled menu
    // command, so an in-execute warning behind it would be dead code.
    expect(term).not.toMatch(/Notification\.warning/);
    expect(fb).not.toMatch(/Notification\.warning/);
  });

  it('a poll tick blocked by the hover guard is deferred at most one tick', () => {
    const poll = method(/private _startPolling[\s\S]*?\n  \}/);
    // Bounded by !_pendingRefresh: a cursor parked over the sidebar never
    // fires mouseleave, so an unbounded guard freezes the list for as long as
    // the user types in the terminal. The second tick refreshes regardless.
    expect(poll).toMatch(/matches\(':hover'\) && !this\._pendingRefresh/);
    expect(poll).toMatch(/this\._pendingRefresh = true/);
    const shell = method(/private _buildShell[\s\S]*?\n  \}/);
    expect(shell).toMatch(/addEventListener\('mouseleave'/);
    // Every refresh path funnels through _fetch, so the flag is cleared there
    // rather than in the flush handler alone.
    const fetch = method(/private async _fetch[\s\S]*?\n  \}/);
    expect(fetch).toMatch(/this\._pendingRefresh = false/);
  });

  it('the mouseleave flush mirrors the poll context-menu guard', () => {
    // Opening a Lumino menu at the cursor fires mouseleave on the body with no
    // pointer movement, so an unguarded flush rebuilds the rows the open menu
    // is acting on - the exact case the poll's isAttached check prevents.
    const shell = method(/private _buildShell[\s\S]*?\n  \}/);
    const flush = (shell.match(
      /addEventListener\('mouseleave'[\s\S]*?\n {4}\}\);/
    ) ?? [''])[0];
    expect(flush).toMatch(/this\._contextMenu\.isAttached/);
  });

  it('empty states distinguish "nothing on disk" from "no matches"', () => {
    expect(render).toMatch(/No Kimi Code sessions found/);
    expect(renderSection).toMatch(/'No matches\.'/);
    expect(renderSection).toMatch(/'No favorites yet\.'/);
    expect(renderSection).toMatch(/'Empty\.'/);
  });
});
