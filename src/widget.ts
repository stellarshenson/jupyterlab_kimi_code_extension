import { JupyterFrontEnd } from '@jupyterlab/application';
import {
  Clipboard,
  Dialog,
  InputDialog,
  Notification,
  showDialog
} from '@jupyterlab/apputils';
import { ServerConnection } from '@jupyterlab/services';
import { IDefaultFileBrowser } from '@jupyterlab/filebrowser';
import { ITerminalTracker } from '@jupyterlab/terminal';
import { IColourfulTabs } from 'jupyterlab_colourful_tab_extension';
import {
  closeIcon,
  copyIcon,
  folderIcon,
  terminalIcon
} from '@jupyterlab/ui-components';
import { CommandRegistry } from '@lumino/commands';
import { Menu, Widget } from '@lumino/widgets';
import { Message } from '@lumino/messaging';

import { requestAPI } from './request';
import { ITerminalColourInfo, colourForTerminal } from './colour';
import {
  addIcon,
  branchIcon,
  switchIcon,
  kimiIcon,
  filterIcon,
  refreshIcon,
  removeIcon,
  shieldIcon,
  starFilledIcon
} from './icons';
import {
  IBranch,
  IBranchesResponse,
  IDeleteBranchesRequest,
  IDeleteBranchesResponse,
  IFavouriteRequest,
  IFavouriteResponse,
  IForkRequest,
  IForkResponse,
  ILaunchTerminalRequest,
  ILaunchTerminalResponse,
  ICleanupRequest,
  ICleanupResponse,
  IRemoveRequest,
  IRemoveResponse,
  ISession,
  ISessionsListResponse,
  ISwitchRequest,
  ISwitchResponse,
  ITerminalCwdResponse
} from './types';

const POLL_INTERVAL_MS = 30_000;
// Shared "recently active" threshold: drives both the row highlight and the
// 'now' relative-time label so the two cues never drift apart.
const RECENTLY_ACTIVE_MS = 60_000;
const DEFAULT_RECENT_LIMIT = 10;
const EXPANDED_STORAGE_KEY = 'jupyterlab_kimi_code_extension:expanded';

type SectionKey = 'favourites' | 'recent' | 'all';

export type PresentationMode = 'name' | 'path';

const DEFAULT_PRESENTATION_MODE: PresentationMode = 'name';

// Colour resolution lives in ./colour - pure, JupyterLab-free, and therefore
// executable under jest (the tint regressed twice behind a green suite).

const SECTION_LABELS: Record<SectionKey, string> = {
  favourites: 'Favorites',
  recent: 'Recent',
  all: 'All'
};

const DEFAULT_EXPANDED: Record<SectionKey, boolean> = {
  favourites: true,
  recent: true,
  all: true
};

function loadExpanded(): Record<SectionKey, boolean> {
  try {
    const raw = window.localStorage.getItem(EXPANDED_STORAGE_KEY);
    if (!raw) {
      return { ...DEFAULT_EXPANDED };
    }
    const parsed = JSON.parse(raw);
    return {
      favourites:
        typeof parsed?.favourites === 'boolean'
          ? parsed.favourites
          : DEFAULT_EXPANDED.favourites,
      recent:
        typeof parsed?.recent === 'boolean'
          ? parsed.recent
          : DEFAULT_EXPANDED.recent,
      all: typeof parsed?.all === 'boolean' ? parsed.all : DEFAULT_EXPANDED.all
    };
  } catch (_err) {
    return { ...DEFAULT_EXPANDED };
  }
}

function saveExpanded(state: Record<SectionKey, boolean>): void {
  try {
    window.localStorage.setItem(EXPANDED_STORAGE_KEY, JSON.stringify(state));
  } catch (_err) {
    // localStorage unavailable (private mode, quota) - ignore
  }
}

export class KimiCodeSessionsWidget extends Widget {
  constructor(
    app: JupyterFrontEnd,
    rootDir: string,
    terminalTracker: ITerminalTracker | null = null,
    fileBrowser: IDefaultFileBrowser | null = null,
    colourfulTabs: IColourfulTabs | null = null
  ) {
    super();
    this._app = app;
    this._serverSettings = app.serviceManager.serverSettings;
    this._rootDir = rootDir.replace(/\/+$/, '');
    this._terminalTracker = terminalTracker;
    this._fileBrowser = fileBrowser;
    this._colourfulTabs = colourfulTabs;

    this.id = 'jupyterlab-kimi-code-extension';
    this.title.icon = kimiIcon;
    this.title.caption = 'Kimi Code Sessions';
    this.addClass('jp-KimiSessionsPanel');

    this._buildShell();
    this._setupContextMenu();
  }

  refresh(): void {
    this._setLoading(true);
    this._setRefreshSpinning(true);
    // `_fetch` is filesystem-fast, so without a floor the spinner would show
    // for a single frame and read as "nothing happened". Hold it for at least
    // ~500 ms so the click visibly registers as a full re-poll.
    const minSpin = new Promise<void>(resolve =>
      window.setTimeout(resolve, 500)
    );
    Promise.all([
      this._fetch().catch(err => this._showError(err)),
      minSpin
    ]).finally(() => {
      this._setRefreshSpinning(false);
      this._setLoading(false);
    });
  }

  /** Choose how rows are labelled: by name (session name, initially the
   * folder name), or by path. */
  setPresentationMode(mode: PresentationMode): void {
    if (this._presentationMode === mode) {
      return;
    }
    this._presentationMode = mode;
    this._render();
  }

  /** Set how many rows the Recent section displays. */
  setRecentLimit(n: number): void {
    const clamped = Math.max(1, Math.min(100, Math.floor(n)));
    if (this._recentLimit === clamped) {
      return;
    }
    this._recentLimit = clamped;
    this._render();
  }

  /** Toggle the --yolo flag on launched sessions. */
  setYoloMode(on: boolean): void {
    this._yoloMode = !!on;
  }

  /** Turn tab tinting on or off. Off drops the tint from Kimi terminals at
   * once rather than waiting for a reload; on re-tints from current colours. */
  setColouredTabs(on: boolean): void {
    const enabled = !!on;
    if (this._colouredTabs === enabled) {
      return;
    }
    this._colouredTabs = enabled;
    void (enabled
      ? this._reconcileTerminalColours()
      : this._clearTerminalColours());
  }

  protected onAfterShow(_msg: Message): void {
    this.refresh();
    this._startPolling();
  }

  protected onBeforeHide(_msg: Message): void {
    this._stopPolling();
  }

  protected onCloseRequest(msg: Message): void {
    this._stopPolling();
    super.onCloseRequest(msg);
  }

  // ------------------------------------------------------------------ shell

  private _buildShell(): void {
    const root = this.node;
    root.innerHTML = '';

    const header = document.createElement('div');
    header.className = 'jp-KimiSessionsPanel-header';

    const title = document.createElement('span');
    title.className = 'jp-KimiSessionsPanel-title';
    title.textContent = 'Kimi Code Sessions';
    header.appendChild(title);

    const newBtn = document.createElement('button');
    newBtn.className = 'jp-KimiSessionsPanel-iconButton';
    newBtn.title = 'New Kimi session in the current folder';
    addIcon.element({ container: newBtn });
    newBtn.addEventListener('click', () => {
      // Drop the menu just below the button, left-aligned with it.
      const rect = newBtn.getBoundingClientRect();
      this._newSessionMenu.open(rect.left, rect.bottom);
    });
    header.appendChild(newBtn);

    const filterBtn = document.createElement('button');
    filterBtn.className = 'jp-KimiSessionsPanel-iconButton';
    filterBtn.title = 'Filter sessions';
    filterIcon.element({ container: filterBtn });
    filterBtn.addEventListener('click', () => this._toggleFilterBar());
    header.appendChild(filterBtn);
    this._filterBtn = filterBtn;

    const refreshBtn = document.createElement('button');
    refreshBtn.className = 'jp-KimiSessionsPanel-iconButton';
    refreshBtn.title = 'Refresh';
    refreshIcon.element({ container: refreshBtn });
    refreshBtn.addEventListener('click', () => this.refresh());
    header.appendChild(refreshBtn);
    this._refreshBtn = refreshBtn;

    // Input and its clear button share a wrapper so the button can sit inside
    // the field's right edge. The wrapper carries the hidden toggle, so the
    // button never outlives the input it belongs to.
    const searchWrap = document.createElement('div');
    searchWrap.className = 'jp-KimiSessionsPanel-searchWrap';
    // Hidden by default; the filter-icon button reveals it. The ``hidden``
    // attribute toggles ``display: none`` via the user-agent stylesheet, so
    // the wrapper's ``display: flex`` must not override it (see base.css).
    searchWrap.hidden = true;

    const search = document.createElement('input');
    search.type = 'search';
    search.className = 'jp-KimiSessionsPanel-search';
    search.placeholder = 'Filter sessions...';
    search.spellcheck = false;
    search.addEventListener('input', () => {
      this._filter = search.value;
      this._syncSearchClear();
      this._render();
    });
    this._searchEl = search;

    const searchClear = document.createElement('button');
    searchClear.className = 'jp-KimiSessionsPanel-searchClear';
    searchClear.title = 'Clear filter';
    searchClear.hidden = true;
    closeIcon.element({ container: searchClear });
    searchClear.addEventListener('click', () => {
      this._filter = '';
      search.value = '';
      this._syncSearchClear();
      this._render();
      // Clearing is a step in filtering, not the end of it - keep the caret
      // in the field so the next query can be typed straight away.
      search.focus();
    });
    this._searchClearEl = searchClear;

    searchWrap.appendChild(search);
    searchWrap.appendChild(searchClear);
    this._searchWrapEl = searchWrap;

    const body = document.createElement('div');
    body.className = 'jp-KimiSessionsPanel-body';
    // A poll tick blocked by the hover guard (see _startPolling) is deferred,
    // not dropped - a cursor merely parked over the sidebar would otherwise
    // freeze the list indefinitely. Flush the deferred tick on the way out.
    body.addEventListener('mouseleave', () => {
      if (!this._pendingRefresh) {
        return;
      }
      this._pendingRefresh = false;
      this._fetch().catch(err =>
        console.error('[jupyterlab_kimi_code_extension]', err)
      );
    });

    // Refresh veil + spinner, shown only during an explicit refresh. It lives
    // on the root (not the body) so `_render` - which wipes the body - never
    // removes it.
    const loading = document.createElement('div');
    loading.className = 'jp-KimiSessionsPanel-loading';
    loading.hidden = true;
    const loadingSpinner = document.createElement('div');
    loadingSpinner.className =
      'jp-kimi-sessions-panel-spinner jp-KimiSessionsPanel-loadingSpinner';
    loading.appendChild(loadingSpinner);

    root.appendChild(header);
    root.appendChild(searchWrap);
    root.appendChild(body);
    root.appendChild(loading);

    this._bodyEl = body;
    this._loadingEl = loading;
  }

  /** Show / hide the filter input. Hiding also clears the active filter
   * so the user does not end up with an "invisible" filter narrowing
   * the rows the next time they open the panel.
   */
  private _toggleFilterBar(): void {
    if (!this._searchEl || !this._searchWrapEl) {
      return;
    }
    const show = this._searchWrapEl.hidden;
    this._searchWrapEl.hidden = !show;
    if (this._filterBtn) {
      this._filterBtn.classList.toggle('jp-mod-active', show);
    }
    if (show) {
      this._searchEl.focus();
    } else if (this._filter) {
      this._filter = '';
      this._searchEl.value = '';
      this._syncSearchClear();
      this._render();
    }
  }

  /** Show the clear button only while the field has text - an "x" hovering
   * over an empty box is noise, and there is nothing to clear. */
  private _syncSearchClear(): void {
    if (this._searchClearEl) {
      this._searchClearEl.hidden = !this._searchEl?.value;
    }
  }

  /** Normalise strings for filter comparison: NFD-decompose, strip combining
   * diacritic marks, lowercase, and collapse separators (`-`, `_`, `.`, `/`,
   * whitespace) entirely. So "foo-bar", "foo_bar", "foo bar", "Foo Bar" all
   * compare equal as "foobar".
   */
  private _normalize(s: string): string {
    return s
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .replace(/[\s\-_./]+/g, '');
  }

  /** Fuzzy match at a 95% threshold: substring on normalised strings,
   * with up to 5% Levenshtein tolerance. For short queries the budget
   * still rounds to zero so behaviour is substring-only there - the
   * relaxation only kicks in for queries long enough that 5% reaches a
   * full edit (10+ chars).
   */
  private _fuzzyMatch(haystack: string, needle: string): boolean {
    if (!needle) {
      return true;
    }
    const h = this._normalize(haystack);
    const n = this._normalize(needle);
    if (!n) {
      return true;
    }
    if (h.includes(n)) {
      return true;
    }
    const tol = Math.round(n.length * 0.05);
    if (tol === 0) {
      return false;
    }
    for (let len = n.length - tol; len <= n.length + tol; len += 1) {
      if (len <= 0) {
        continue;
      }
      for (let i = 0; i + len <= h.length; i += 1) {
        if (this._levenshtein(h.slice(i, i + len), n) <= tol) {
          return true;
        }
      }
    }
    return false;
  }

  private _levenshtein(a: string, b: string): number {
    const m = a.length;
    const n = b.length;
    if (m === 0) {
      return n;
    }
    if (n === 0) {
      return m;
    }
    const dp: number[] = new Array(n + 1);
    for (let j = 0; j <= n; j += 1) {
      dp[j] = j;
    }
    for (let i = 1; i <= m; i += 1) {
      let prev = dp[0];
      dp[0] = i;
      for (let j = 1; j <= n; j += 1) {
        const tmp = dp[j];
        dp[j] =
          a[i - 1] === b[j - 1] ? prev : 1 + Math.min(prev, dp[j], dp[j - 1]);
        prev = tmp;
      }
    }
    return dp[n];
  }

  private _matchesFilter(s: ISession): boolean {
    const q = this._filter.trim();
    if (!q) {
      return true;
    }
    return (
      this._fuzzyMatch(s.name, q) ||
      this._fuzzyMatch(s.project_path, q) ||
      this._fuzzyMatch(this._lookupName(s), q)
    );
  }

  /** Raise or clear the full-panel refresh veil. Only the explicit refresh
   * path calls this; the background poll fetches silently so the panel never
   * flashes a spinner on its own. */
  private _setLoading(on: boolean): void {
    if (this._loadingEl) {
      this._loadingEl.hidden = !on;
    }
  }

  private _showError(err: unknown): void {
    const message = err instanceof Error ? err.message : String(err);
    console.error('[jupyterlab_kimi_code_extension]', message);
    Notification.error(message, { autoClose: 4000 });
  }

  // ------------------------------------------------------------------ data

  private async _fetch(): Promise<void> {
    // `cache: 'no-store'` so the manual refresh button (and the post-launch
    // refresh) always re-read the server's view of ~/.kimi-code rather than
    // a possibly-stale browser-cached response.
    const data = await requestAPI<ISessionsListResponse>(
      'sessions',
      this._serverSettings,
      { cache: 'no-store' }
    );
    this._sessions = data.sessions ?? [];
    this._render();
    // Best-effort tab tinting; the method self-catches, so a colour failure
    // never breaks the fetch.
    void this._reconcileTerminalColours();
  }

  private async _toggleFavourite(session: ISession): Promise<void> {
    const next = !session.favourite;
    // Optimistic update
    session.favourite = next;
    this._render();
    const body: IFavouriteRequest = {
      project_path: session.project_path,
      favourite: next
    };
    try {
      await requestAPI<IFavouriteResponse>(
        'sessions/favourite',
        this._serverSettings,
        { method: 'POST', body: JSON.stringify(body) }
      );
    } catch (err) {
      // Roll back on failure - console-only, the visual rollback is the cue.
      session.favourite = !next;
      this._render();
      console.error('[jupyterlab_kimi_code_extension]', err);
    }
  }

  private async _remove(session: ISession): Promise<void> {
    const name = this._lookupName(session);
    const confirm = await showDialog({
      title: 'Remove from Kimi',
      body:
        `Remove "${name}" from Kimi? This deletes the entire Kimi ` +
        'project and every conversation it holds; they are moved to trash ' +
        "(or deleted permanently when JupyterLab's move-to-trash setting " +
        'is off).',
      buttons: [Dialog.cancelButton(), Dialog.warnButton({ label: 'Remove' })]
    });
    if (!confirm.button.accept) {
      return;
    }

    this._removingPaths.add(session.encoded_path);
    this._render();
    const body: IRemoveRequest = { encoded_path: session.encoded_path };
    try {
      await requestAPI<IRemoveResponse>(
        'sessions/remove',
        this._serverSettings,
        { method: 'POST', body: JSON.stringify(body) }
      );
      // Drop locally and re-render; a full refresh will follow on next poll
      this._sessions = (this._sessions ?? []).filter(
        s => s.encoded_path !== session.encoded_path
      );
    } catch (err) {
      this._showError(err);
    } finally {
      this._removingPaths.delete(session.encoded_path);
      this._render();
    }
  }

  private async _cleanupParallel(session: ISession): Promise<void> {
    const extra = session.extra_sessions;
    const name = this._lookupName(session);
    const confirm = await showDialog({
      title: 'Clean Up Parallel Sessions',
      body:
        `Remove ${extra} parallel session${extra === 1 ? '' : 's'} from ` +
        `"${name}"? The main conversation is kept; the rest are moved to ` +
        "trash (or deleted permanently when JupyterLab's move-to-trash " +
        'setting is off).',
      buttons: [Dialog.cancelButton(), Dialog.warnButton({ label: 'Remove' })]
    });
    if (!confirm.button.accept) {
      return;
    }

    const body = new Widget();
    body.node.className = 'jp-KimiSessionsPanel-cleanupBody';

    const message = document.createElement('div');
    const count = session.extra_sessions;
    message.textContent = `Removing ${count} parallel session${
      count === 1 ? '' : 's'
    }...`;
    body.node.appendChild(message);

    // No `value` attribute -> indeterminate (animated) while the request is
    // in flight; set to max on completion so the bar reads as finished.
    const bar = document.createElement('progress');
    bar.className = 'jp-KimiSessionsPanel-cleanupProgress';
    bar.max = 1;
    // The UA animates an indeterminate <progress> and author CSS cannot stop
    // it, so the reduced-motion block in base.css can't reach this one - make
    // the bar determinate (still, at zero) instead.
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      bar.value = 0;
    }
    body.node.appendChild(bar);

    const dialog = new Dialog<unknown>({
      title: 'Clean Up Parallel Sessions',
      body,
      buttons: [Dialog.okButton({ label: 'Close' })]
    });
    // Hide the Close button while work is in progress; restore it once the
    // outcome (success or error) is shown so the user dismisses the popup.
    const footer = dialog.node.querySelector(
      '.jp-Dialog-footer'
    ) as HTMLElement | null;
    if (footer) {
      footer.style.display = 'none';
    }
    void dialog.launch();

    const cleanupBody: ICleanupRequest = {
      encoded_path: session.encoded_path
    };
    try {
      const data = await requestAPI<ICleanupResponse>(
        'sessions/cleanup',
        this._serverSettings,
        { method: 'POST', body: JSON.stringify(cleanupBody) }
      );
      bar.value = 1;
      message.textContent = `Removed ${data.removed_count} parallel session${
        data.removed_count === 1 ? '' : 's'
      }.`;
      // Refresh so the row's extra_sessions count (and menu label) update
      await this._fetch();
    } catch (err) {
      bar.remove();
      message.classList.add('jp-KimiSessionsPanel-cleanupError');
      message.textContent = `Cleanup failed: ${
        err instanceof Error ? err.message : String(err)
      }`;
      // The inline message above IS the user-facing surface here (the dialog
      // stays open and its Close button is restored) - a toast carrying the
      // same string would double-report.
      console.error('[jupyterlab_kimi_code_extension]', err);
    } finally {
      if (footer) {
        footer.style.display = '';
      }
    }
  }

  // -------------------------------------------------------------- terminal

  private async _resumeInTerminal(
    session: ISession,
    forceYolo: boolean = false
  ): Promise<void> {
    // Coalesce concurrent clicks on the SAME conversation - subsequent clicks
    // attach to the in-flight promise instead of creating their own terminal.
    // The key is per-conversation, so opening a different branch of the same
    // project launches independently rather than coalescing onto this one.
    const key = `${session.project_path}\n${session.session_id}`;
    const inFlight = this._pendingByPath.get(key);
    if (inFlight) {
      return inFlight;
    }
    const promise = this._doResumeInTerminal(session, forceYolo).finally(() => {
      this._pendingByPath.delete(key);
    });
    this._pendingByPath.set(key, promise);
    return promise;
  }

  /**
   * Reuse an open terminal only when it is POSITIVELY running the
   * conversation the caller wants (its kimi argv carries the same
   * ``-S``/``--session`` id); otherwise launch a fresh ``kimi -S <id>``.
   *
   * A cwd-matching terminal whose conversation is UNKNOWN (kimi started
   * with ``-c`` or a bare ``kimi``, so no id is in its argv) is never
   * reused - it may be running a different conversation of this project,
   * which is the switch-then-click bug. Every terminal the extension
   * launches for a resume carries an explicit id (``-S <id>``), so an
   * unknown terminal is necessarily one the extension did not start for
   * that conversation.
   */
  private async _doResumeInTerminal(
    session: ISession,
    forceYolo: boolean
  ): Promise<void> {
    try {
      // Always prefer reusing an open terminal for this conversation. The
      // yolo flag can only be applied to a fresh pty, never retroactively.
      // So if the user wants yolo mode but an open terminal already exists,
      // show a modal asking them to close it first - we won't auto-close,
      // won't silently reuse the wrong mode.

      // 1. In-memory microcache (most-recent terminal for this project).
      // Reuse it only when it is running the wanted conversation.
      const cached = this._terminalsByPath.get(session.project_path);
      if (
        cached &&
        !cached.widget.isDisposed &&
        cached.sessionId === session.session_id
      ) {
        if (forceYolo) {
          await this._showCloseExistingDialog();
        }
        this._focusTerminal(cached.widget);
        return;
      }

      // 2. Walk every live terminal widget JL knows about.
      const found = await this._findTerminalForSession(session.session_id);
      if (found) {
        // Tag the cache with the OBSERVED conversation - here it equals the
        // wanted id (the gate in _findTerminalForSession), so a later reuse
        // trusts a confirmed conversation rather than a wish.
        this._terminalsByPath.set(session.project_path, {
          widget: found.widget,
          sessionId: found.runningId ?? undefined
        });
        this._wireTerminalDisposal(session.project_path, found.widget);
        if (forceYolo) {
          await this._showCloseExistingDialog();
        }
        this._focusTerminal(found.widget);
        return;
      }

      // 3. No matching terminal - spawn a new one with `kimi -S <id>`
      // as the pty's only process (no shell). Server-side endpoint calls
      // terminal_manager.create(shell_command=[kimi, -S, sid], cwd=...)
      // and returns the terminal name; we then attach JL's standard widget
      // via terminal:open. When kimi exits, the tab closes. The launch
      // RPC + the WebSocket-resize waiter on the server can take a few
      // seconds, so show a modal spinner for visual feedback.
      const spinner = this._showLaunchSpinner();
      try {
        // Just name the conversation. The server decides the final argv at
        // launch (resume vs new, yolo append) because only it is
        // authoritative at that moment.
        const body: ILaunchTerminalRequest = {
          project_path: session.project_path,
          session_id: session.session_id,
          yolo: forceYolo || this._yoloMode
        };
        const launched = await requestAPI<ILaunchTerminalResponse>(
          'launch-terminal',
          this._serverSettings,
          { method: 'POST', body: JSON.stringify(body) }
        );
        const widget: any = await this._app.commands.execute('terminal:open', {
          name: launched.terminal_name
        });
        if (widget?.id) {
          this._terminalsByPath.set(session.project_path, {
            widget,
            sessionId: session.session_id
          });
          this._wireTerminalDisposal(session.project_path, widget);
          this._focusTerminal(widget);
        }
      } finally {
        spinner.dispose();
      }
    } catch (err) {
      this._showError(err);
    } finally {
      // Reuse or fresh launch, either way the picture changed (a row may
      // have appeared). Pull fresh state.
      void this._fetch().catch(() => {
        /* a poll tick will retry; nothing actionable here */
      });
    }
  }

  /** Absolute path of the file browser's current folder; falls back to the
   * server root when no file browser is available. */
  private _currentFolder(): string {
    const rel = (this._fileBrowser?.model?.path ?? '').replace(/^\/+/, '');
    return rel ? `${this._rootDir}/${rel}` : this._rootDir;
  }

  /** Start a brand-new kimi session in the file browser's current folder.
   * Same launch path as resuming (kimi is the pty's only process via the
   * launch-terminal endpoint) - just without -S, and always a fresh
   * terminal since there is no existing session to reuse. Kimi assigns the
   * session id itself, so the launch carries no pre-assigned id and the new
   * conversation surfaces on the next refresh.
   */
  private async _newSession(forceYolo: boolean): Promise<void> {
    const projectPath = this._currentFolder();
    if (!projectPath) {
      return;
    }
    const spinner = this._showLaunchSpinner();
    try {
      const body: ILaunchTerminalRequest = {
        project_path: projectPath,
        yolo: forceYolo || this._yoloMode
      };
      const launched = await requestAPI<ILaunchTerminalResponse>(
        'launch-terminal',
        this._serverSettings,
        { method: 'POST', body: JSON.stringify(body) }
      );
      const widget: any = await this._app.commands.execute('terminal:open', {
        name: launched.terminal_name
      });
      if (widget?.id) {
        // The new conversation's id is assigned by kimi and unknown here, so
        // the cache entry carries no id - an unknown id is never reused
        // (claude DEF-4); the row's resume will re-probe and refocus by
        // identity.
        this._terminalsByPath.set(projectPath, {
          widget,
          sessionId: undefined
        });
        this._wireTerminalDisposal(projectPath, widget);
        this._focusTerminal(widget);
      }
    } catch (err) {
      this._showError(err);
    } finally {
      spinner.dispose();
      // The new session creates a session dir under ~/.kimi-code - refresh
      // so its row appears without waiting a poll.
      void this._fetch().catch(() => {
        /* a poll tick will retry; nothing actionable here */
      });
    }
  }

  /**
   * Bring a terminal tab to the front AND hand it keyboard focus, so the
   * user can start typing without an extra click. `activateById` only
   * reveals the tab; the xterm inside doesn't always grab DOM focus,
   * especially when the click originated in this sidebar. We defer the
   * `term.focus()` to the next frame so the widget is attached and visible
   * first.
   */
  private _focusTerminal(widget: any): void {
    if (!widget || widget.isDisposed) {
      return;
    }
    this._app.shell.activateById(widget.id);
    requestAnimationFrame(() => {
      try {
        widget.content?.term?.focus?.();
      } catch (_err) {
        /* terminal may have been disposed in the meantime - ignore */
      }
    });
  }

  /** Tint a terminal's dock tab with the colour derived from the Kimi
   * session it runs, delegating to jupyterlab_colourful_tab_extension's
   * `setColour` API so that extension owns the tab CSS and colour
   * vocabulary. A no-op when the colourful-tab extension is not installed
   * (the token is optional). An absent/unknown colour clears the tint. */
  private _applyTerminalColour(
    widget: any,
    colourId: string | null | undefined
  ): void {
    if (!this._colourfulTabs || !widget || widget.isDisposed) {
      return;
    }
    this._colourfulTabs.setColour(widget, colourId ?? null);
  }

  /** Re-tint EVERY open Kimi terminal tab from the freshest session ids,
   * whether the plugin launched it or the user opened it themselves. Walks
   * JupyterLab's terminal tracker (the registry of all open terminals) rather
   * than only the plugin's own launch cache, and re-resolves each terminal's
   * Kimi conversation on every pass via the ``terminal-cwd`` probe - so a tab
   * whose terminal has since started (or switched) a Kimi conversation is
   * re-tinted correctly rather than pinned to a stale identity. The probe is
   * a few /proc reads per terminal; runs after each fetch (launch refresh +
   * the 30s poll), which is already gated to skip while a context menu is
   * open. */
  private async _reconcileTerminalColours(): Promise<void> {
    if (!this._colouredTabs) {
      return;
    }
    try {
      const sessions = this._sessions ?? [];
      await this._eachKimiTerminal((widget, info) => {
        this._applyTerminalColour(widget, colourForTerminal(info, sessions));
      });
    } catch (_err) {
      // Best-effort tinting - never surface a colour failure to callers.
    }
  }

  /** Drop the tint from every Kimi terminal - the coloured-tabs setting went
   * off. Only Kimi terminals are touched, so a tint the user set by hand on
   * some other tab survives. */
  private async _clearTerminalColours(): Promise<void> {
    try {
      await this._eachKimiTerminal(widget => {
        this._applyTerminalColour(widget, null);
      });
    } catch (_err) {
      // Best-effort clearing - never surface a colour failure to callers.
    }
  }

  /** Probe every open terminal and hand each one running Kimi to `apply`. */
  private async _eachKimiTerminal(
    apply: (widget: any, info: ITerminalColourInfo) => void
  ): Promise<void> {
    if (!this._colourfulTabs || !this._terminalTracker) {
      return;
    }
    const widgets: any[] = [];
    this._terminalTracker.forEach((widget: any) => {
      if (widget && !widget.isDisposed) {
        widgets.push(widget);
      }
    });
    await Promise.all(
      widgets.map(async widget => {
        const info = await this._interrogateTerminal(widget);
        if (!info || !info.hasKimi || widget.isDisposed) {
          return;
        }
        apply(widget, info);
      })
    );
  }

  /** Probe a terminal: runs Kimi?, which conversation, its cwd(s). Probes
   * EVERY terminal every pass - the launch cache is not consulted, it pins
   * the launch-time conversation and goes stale on an in-place switch. Null
   * when no session name yet, or the probe fails. */
  private async _interrogateTerminal(widget: any): Promise<{
    hasKimi: boolean;
    sessionId: string | null;
    cwds: string[];
  } | null> {
    const sessName: string | undefined = widget?.content?.session?.name;
    if (typeof sessName !== 'string' || !sessName) {
      return null;
    }
    try {
      const data = await requestAPI<ITerminalCwdResponse>(
        `terminal-cwd/${encodeURIComponent(sessName)}`,
        this._serverSettings
      );
      return {
        hasKimi: !!data?.has_kimi,
        sessionId: data?.session_id ?? null,
        cwds: Array.isArray(data?.cwds) ? data.cwds : []
      };
    } catch (_err) {
      // Backend may 404 for a terminal that vanished between enumeration and
      // probe - treat as unresolved and retry on the next poll.
      return null;
    }
  }

  /** Find an open terminal running the wanted conversation, or null.
   *
   * Matches on the conversation id ALONE. The backend reads each terminal's
   * session id from the running kimi's argv (``-S``/``--session``), so a
   * conversation the extension resumed is identified positively, while a
   * bare `kimi` / `-c` the user opened reports an unknown id. A session id
   * is globally unique to one conversation, so a terminal running it IS the
   * one to focus - regardless of its reported cwd (a kimi that cd'd into a
   * subdir, or whose project dir was recreated, must still be reused, not
   * duplicated). Reuse stays strict on identity: a terminal whose running
   * session cannot be read (no kimi, or an unreadable id) reports null,
   * which never equals a wanted id, so a DIFFERENT conversation is never
   * focused by mistake (claude DEF-4). */
  private async _findTerminalForSession(
    wantedSessionId: string
  ): Promise<{ widget: any; runningId: string | null } | null> {
    if (!this._terminalTracker || !wantedSessionId) {
      return null;
    }
    const candidates: any[] = [];
    this._terminalTracker.forEach((widget: any) => {
      if (widget && !widget.isDisposed) {
        candidates.push(widget);
      }
    });
    for (const widget of candidates) {
      // Positive identity match on the running conversation, resolved via
      // the shared _interrogateTerminal probe. The backend reports a session
      // id only for a live kimi with a resumable argv, so a match implies
      // has_kimi - the id alone is the gate, and the terminal's cwd is
      // irrelevant once its conversation is positively known.
      const info = await this._interrogateTerminal(widget);
      const runningId = info?.sessionId ?? null;
      if (runningId === wantedSessionId) {
        return { widget, runningId };
      }
    }
    return null;
  }

  private async _showCloseExistingDialog(): Promise<void> {
    await showDialog({
      title: 'Existing Kimi session is running',
      body:
        'A terminal for this project is already open. To launch with ' +
        '--yolo, close that terminal first then ' +
        'click "Resume (YOLO)" again.',
      buttons: [Dialog.okButton({ label: 'OK' })]
    });
  }

  /** Show a modal with a spinner while the terminal is being launched. The
   * caller must dismiss it via ``.dispose()`` once the work is done - the
   * dialog has no buttons so ``.resolve()`` would be a no-op.
   */
  private _showLaunchSpinner(): Dialog<unknown> {
    const body = new Widget();
    body.node.className = 'jp-KimiSessionsPanel-launchOverlay';

    const spinner = document.createElement('div');
    spinner.className =
      'jp-kimi-sessions-panel-spinner jp-KimiSessionsPanel-launchSpinner';
    body.node.appendChild(spinner);

    const dialog = new Dialog<unknown>({
      title: 'Opening Kimi Code session',
      body,
      buttons: []
    });
    // launch() returns a Promise we don't await - the caller dismisses this
    // dialog with .dispose() when the spawn completes. Disposing an open Lumino
    // dialog rejects that promise with `undefined`, so catch it here to keep a
    // benign teardown from surfacing as an unhandled promise rejection.
    dialog.launch().catch(() => undefined);
    return dialog;
  }

  private _wireTerminalDisposal(projectPath: string, widget: any): void {
    if (!widget?.disposed?.connect) {
      return;
    }
    widget.disposed.connect(() => {
      if (this._terminalsByPath.get(projectPath)?.widget === widget) {
        this._terminalsByPath.delete(projectPath);
      }
    });
  }

  // -------------------------------------------------------------- rendering

  /** Apply the presentation-mode setting (basename collisions for name
   * mode are resolved separately in ``_disambiguate``). */
  private _displayName(s: ISession): string {
    const folder = this._basename(s.project_path) || s.encoded_path;
    if (this._presentationMode === 'path') {
      return this._displayPath(s.project_path) || folder;
    }
    // Honour the session name Kimi records (a custom title); fall back
    // to the folder basename when the backend reports no session name.
    if (s.name_source === 'session' && s.name) {
      return s.name;
    }
    return folder;
  }

  private _basename(p: string): string {
    if (!p) {
      return '';
    }
    const parts = p.split('/').filter(Boolean);
    return parts[parts.length - 1] || '';
  }

  /** Walk path tails until every name in a colliding group is unique.
   * Folder-mode labels are folder basenames, so two different projects can
   * end up with the same label (e.g. two `datascience` folders under
   * different parents); we extend each colliding label with as much of its
   * parent path as it takes to make it unique. */
  private _disambiguate(rows: ISession[]): Map<string, string> {
    const out = new Map<string, string>();
    const groups = new Map<string, ISession[]>();
    for (const r of rows) {
      const n = this._displayName(r);
      groups.set(n, (groups.get(n) ?? []).concat(r));
    }
    for (const [name, group] of groups.entries()) {
      if (group.length === 1) {
        out.set(group[0].project_path, name);
        continue;
      }
      const segs = group.map(r => r.project_path.split('/').filter(Boolean));
      const max = Math.max(...segs.map(s => s.length));
      let depth = 1;
      let resolved = false;
      while (depth <= max) {
        const tails = segs.map(s => s.slice(-depth).join('/'));
        if (new Set(tails).size === tails.length) {
          group.forEach((r, i) => out.set(r.project_path, tails[i]));
          resolved = true;
          break;
        }
        depth += 1;
      }
      if (!resolved) {
        // Identical project_path values across rows shouldn't happen
        // (list_sessions dedups by path) but if it ever does, fall back to
        // the absolute path so rows stay distinguishable.
        group.forEach(r => out.set(r.project_path, r.project_path));
      }
    }
    return out;
  }

  private _render(): void {
    const sessions = this._sessions ?? [];

    // Capture scrollTop per section so polling refreshes don't reset the
    // user's place inside the All list. The body scrolls too in short
    // windows (claude DEF-12 safety valve) - clearing innerHTML clamps its
    // scrollTop to 0, so it needs the same capture/restore.
    const bodyScroll = this._bodyEl.scrollTop;
    const scrolls = new Map<string, number>();
    this._bodyEl
      .querySelectorAll<HTMLElement>('.jp-KimiSessionsPanel-section')
      .forEach(sect => {
        const key = sect.dataset.section;
        const list = sect.querySelector<HTMLElement>(
          '.jp-KimiSessionsPanel-list'
        );
        if (key && list) {
          scrolls.set(key, list.scrollTop);
        }
      });

    // Same reason as the scroll capture, for the keyboard: the wipe below
    // destroys the focused row and activeElement falls back to BODY, so a
    // keyboard user loses their place on every poll tick. Record which row
    // held focus and re-focus its rebuilt counterpart below - the Manage
    // Sessions popup restores focus after its own rebuild the same way.
    const active = document.activeElement;
    const focusedRowKey =
      active instanceof HTMLElement && this._bodyEl.contains(active)
        ? (active.closest<HTMLElement>('.jp-KimiSessionsPanel-row')?.dataset
            .rowKey ?? null)
        : null;

    this._bodyEl.innerHTML = '';

    if (sessions.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'jp-KimiSessionsPanel-empty';
      empty.textContent =
        'No Kimi Code sessions found. Use the + button to start one in the ' +
        'current folder.';
      this._bodyEl.appendChild(empty);
      return;
    }

    // Compute disambiguated display names once per render (against the
    // full set so suffixes stay stable when filtering narrows the view).
    this._displayNames = this._disambiguate(sessions);

    const filtered = sessions.filter(s => this._matchesFilter(s));
    const favourites = filtered.filter(s => s.favourite);
    const recent = [...filtered]
      .sort((a, b) => b.file_mtime - a.file_mtime)
      .slice(0, this._recentLimit);
    const all = [...filtered].sort((a, b) =>
      this._lookupName(a).localeCompare(this._lookupName(b))
    );

    if (favourites.length > 0) {
      this._renderSection('favourites', favourites);
    }
    this._renderSection('recent', recent);
    this._renderSection('all', all);

    // Restore scroll positions
    this._bodyEl
      .querySelectorAll<HTMLElement>('.jp-KimiSessionsPanel-section')
      .forEach(sect => {
        const key = sect.dataset.section;
        const list = sect.querySelector<HTMLElement>(
          '.jp-KimiSessionsPanel-list'
        );
        const saved = key ? scrolls.get(key) : undefined;
        if (list && saved !== undefined) {
          list.scrollTop = saved;
        }
      });
    this._bodyEl.scrollTop = bodyScroll;

    // Restore keyboard focus onto the rebuilt row (preventScroll so the
    // scroll positions just restored above are not overridden).
    if (focusedRowKey !== null) {
      const rows = Array.from(
        this._bodyEl.querySelectorAll<HTMLElement>('.jp-KimiSessionsPanel-row')
      );
      const match = rows.find(r => r.dataset.rowKey === focusedRowKey);
      match?.focus({ preventScroll: true });
    }
  }

  private _renderSection(key: SectionKey, items: ISession[]): void {
    const section = document.createElement('div');
    section.className = 'jp-KimiSessionsPanel-section';
    section.dataset.section = key;
    const expanded = this._expanded[key];

    const header = document.createElement('button');
    header.className = 'jp-KimiSessionsPanel-sectionHeader';
    header.setAttribute('aria-expanded', String(expanded));

    const caret = document.createElement('span');
    caret.className = 'jp-KimiSessionsPanel-caret';
    // Decorative - the expanded state is already on aria-expanded above.
    caret.setAttribute('aria-hidden', 'true');
    caret.textContent = expanded ? '▾' : '▸';
    header.appendChild(caret);

    const label = document.createElement('span');
    label.className = 'jp-KimiSessionsPanel-sectionLabel';
    label.textContent = `${SECTION_LABELS[key]} (${items.length})`;
    header.appendChild(label);

    header.addEventListener('click', () => {
      this._expanded[key] = !this._expanded[key];
      saveExpanded(this._expanded);
      this._render();
    });
    section.appendChild(header);

    if (expanded) {
      const list = document.createElement('div');
      list.className = 'jp-KimiSessionsPanel-list';
      if (items.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'jp-KimiSessionsPanel-emptySection';
        // An active filter that leaves a section empty reads as "no matches",
        // not as the section being empty on disk.
        empty.textContent = this._filter.trim()
          ? 'No matches.'
          : key === 'favourites'
            ? 'No favorites yet.'
            : 'Empty.';
        list.appendChild(empty);
      } else {
        for (const item of items) {
          list.appendChild(this._renderRow(item, key));
        }
      }
      section.appendChild(list);
    }

    this._bodyEl.appendChild(section);
  }

  private _renderRow(
    session: ISession,
    sectionKey: SectionKey
  ): HTMLDivElement {
    const row = document.createElement('div');
    row.className = 'jp-KimiSessionsPanel-row';
    // Identity for the focus capture/restore in _render. The section is part
    // of the key because one conversation can be rendered in several sections
    // - focus must return to the row the user was actually on.
    row.dataset.rowKey = [
      sectionKey,
      session.encoded_path,
      session.session_id
    ].join('\n');
    row.title = this._buildRowTooltip(session);

    // Age emphasis: active within the last minute reads bright, idle for
    // over a week dims; the state decays/promotes on the next refresh.
    if (session.file_mtime) {
      const age = Date.now() - session.file_mtime;
      if (age < RECENTLY_ACTIVE_MS) {
        row.classList.add('jp-mod-recentlyActive');
      } else if (age > 7 * 86_400_000) {
        row.classList.add('jp-mod-stale');
      }
    }

    const removing = this._removingPaths.has(session.encoded_path);
    if (removing) {
      row.classList.add('jp-mod-busy');
    }

    if (removing) {
      const spinner = document.createElement('span');
      // The lowercase class carries the shared spin animation; the PascalCase
      // one sizes it down to the 8px placeholder slot (see base.css).
      spinner.className =
        'jp-kimi-sessions-panel-spinner jp-KimiSessionsPanel-spinner';
      spinner.title = 'Removing...';
      row.appendChild(spinner);
    } else {
      // Fixed-width spacer: keeps the name column aligned with rows that
      // are mid-removal (whose spinner occupies this slot).
      const dotPlaceholder = document.createElement('span');
      dotPlaceholder.className = 'jp-KimiSessionsPanel-dotPlaceholder';
      row.appendChild(dotPlaceholder);
    }

    const name = document.createElement('span');
    name.className = 'jp-KimiSessionsPanel-name';
    name.textContent = this._lookupName(session);
    // Branch icon + total conversation count - only when the project has
    // branches. Lives inside the name span so it hugs the label text
    // instead of being flexed to the row's right edge.
    if (session.extra_sessions > 0) {
      const badge = document.createElement('span');
      badge.className = 'jp-KimiSessionsPanel-branchBadge';
      const icon = document.createElement('span');
      icon.className = 'jp-KimiSessionsPanel-branchBadgeIcon';
      branchIcon.element({ container: icon });
      badge.appendChild(icon);
      badge.appendChild(
        document.createTextNode(String(session.extra_sessions + 1))
      );
      name.appendChild(badge);
    }
    row.appendChild(name);

    // No star in the Favorites section - every row there is a favorite
    // by definition; stars are an indicator only useful in Recent/All.
    // Star sits before the time so the fixed-width time column stays the
    // rightmost alignment anchor across all rows.
    if (session.favourite && sectionKey !== 'favourites') {
      const star = document.createElement('span');
      star.className = 'jp-KimiSessionsPanel-favStar';
      star.title = 'Favorite';
      starFilledIcon.element({ container: star });
      row.appendChild(star);
    }

    // Always present (empty without an mtime) so the star column keeps
    // the same anchor across every row in the panel.
    const time = document.createElement('span');
    time.className = 'jp-KimiSessionsPanel-rowTime';
    time.textContent = session.file_mtime
      ? this._formatRelativeTime(session.file_mtime)
      : '';
    row.appendChild(time);

    row.addEventListener('click', () => {
      if (removing) {
        return;
      }
      void this._resumeInTerminal(session);
    });
    row.addEventListener('contextmenu', e => {
      e.preventDefault();
      if (removing) {
        return;
      }
      this._activeSession = session;
      this._setActiveRow(row);
      void this._openContextMenu(session, e.clientX, e.clientY);
    });

    // Keyboard access: rows are focusable buttons - Enter/Space resumes,
    // the ContextMenu key or Shift+F10 opens the context menu at the row.
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    row.addEventListener('keydown', e => {
      if (removing) {
        return;
      }
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        void this._resumeInTerminal(session);
      } else if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
        e.preventDefault();
        this._activeSession = session;
        this._setActiveRow(row);
        const rect = row.getBoundingClientRect();
        void this._openContextMenu(session, rect.left, rect.bottom);
      }
    });

    return row;
  }

  private _lookupName(s: ISession): string {
    return this._displayNames.get(s.project_path) ?? this._displayName(s);
  }

  private _buildRowTooltip(s: ISession): string {
    const lines: string[] = [this._lookupName(s)];
    lines.push(`Path: ${this._displayPath(s.project_path)}`);
    if (s.file_mtime) {
      lines.push(
        `Last activity: ${this._formatRelativeTime(s.file_mtime)} ` +
          `(${this._formatAbsoluteTime(s.file_mtime)})`
      );
    }
    if (s.message_count) {
      lines.push(`Messages: ${s.message_count}`);
    }
    if (s.extra_sessions > 0) {
      lines.push(`Conversations: ${s.extra_sessions + 1}`);
    }
    if (s.git_branch) {
      lines.push(`Branch: ${s.git_branch}`);
    }
    if (s.session_id) {
      lines.push(`Session id: ${s.session_id}`);
    }
    return lines.join('\n');
  }

  private _displayPath(absolute: string): string {
    if (!this._rootDir) {
      return absolute;
    }
    if (absolute === this._rootDir) {
      return '.';
    }
    if (absolute.startsWith(this._rootDir + '/')) {
      return absolute.slice(this._rootDir.length + 1);
    }
    return absolute;
  }

  /** Path relative to the JupyterLab server root (``''`` for the root
   * itself), or ``null`` when the folder lies outside the root - in which
   * case the file browser has no way to address it. */
  private _pathUnderRoot(absolute: string): string | null {
    if (!this._rootDir) {
      return null;
    }
    if (absolute === this._rootDir) {
      return '';
    }
    if (absolute.startsWith(this._rootDir + '/')) {
      return absolute.slice(this._rootDir.length + 1);
    }
    return null;
  }

  private _formatRelativeTime(epochMs: number): string {
    if (!epochMs) {
      return 'unknown';
    }
    const diff = Date.now() - epochMs;
    if (diff < RECENTLY_ACTIVE_MS) {
      return 'now';
    }
    if (diff < 3_600_000) {
      return `${Math.floor(diff / 60_000)}m ago`;
    }
    if (diff < 86_400_000) {
      return `${Math.floor(diff / 3_600_000)}h ago`;
    }
    return `${Math.floor(diff / 86_400_000)}d ago`;
  }

  /** Absolute local timestamp for tooltips: ``YYYY-MM-DD HH:MM``. */
  private _formatAbsoluteTime(epochMs: number): string {
    const d = new Date(epochMs);
    const pad = (n: number): string => String(n).padStart(2, '0');
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
      `${pad(d.getHours())}:${pad(d.getMinutes())}`
    );
  }

  /** Short session id for display: the first 8 chars of the uuid part.
   * Kimi ids carry a constant "session_" prefix, so slicing from the
   * front would render the same string for every conversation. */
  private _shortSessionId(sessionId: string): string {
    return sessionId.startsWith('session_')
      ? sessionId.slice(8, 16)
      : sessionId.slice(0, 8);
  }

  /** Branch entry display: conversation name plus short session id in
   * brackets; branches share the project path so only the name and id
   * distinguish them. Skips the suffix when the label already is the
   * short id (the backend's last-resort fallback). */
  private _branchDisplayName(b: IBranch): string {
    const shortId = this._shortSessionId(b.session_id);
    return b.label === shortId ? b.label : `${b.label} (${shortId})`;
  }

  private _setRefreshSpinning(on: boolean): void {
    if (!this._refreshBtn) {
      return;
    }
    this._refreshBtn.classList.toggle('jp-mod-spinning', on);
  }

  private _setActiveRow(row: HTMLElement | null): void {
    if (this._activeRowEl && this._activeRowEl !== row) {
      this._activeRowEl.classList.remove('jp-mod-active');
    }
    this._activeRowEl = row;
    if (row) {
      row.classList.add('jp-mod-active');
    }
  }

  // -------------------------------------------------------------- ctx menu

  private _setupContextMenu(): void {
    this._commands = new CommandRegistry();

    this._commands.addCommand('kimi-code-sessions:toggle-favourite', {
      label: () =>
        this._activeSession?.favourite
          ? 'Remove from Favorites'
          : 'Add to Favorites',
      icon: starFilledIcon,
      execute: () => {
        if (this._activeSession) {
          void this._toggleFavourite(this._activeSession);
        }
      }
    });

    this._commands.addCommand('kimi-code-sessions:resume', {
      label: 'Resume',
      execute: () => {
        if (this._activeSession) {
          void this._resumeInTerminal(this._activeSession);
        }
      }
    });

    this._commands.addCommand('kimi-code-sessions:resume-yolo', {
      label: 'Resume (YOLO)',
      icon: shieldIcon,
      execute: () => {
        if (this._activeSession) {
          void this._resumeInTerminal(this._activeSession, true);
        }
      }
    });

    this._commands.addCommand('kimi-code-sessions:open-terminal', {
      label: 'Open Terminal',
      icon: terminalIcon,
      // A folder outside the JupyterLab root cannot host a terminal (the
      // cwd argument is contents-manager relative) - grey the item out.
      isEnabled: () =>
        !!this._activeSession &&
        this._pathUnderRoot(this._activeSession.project_path) !== null,
      execute: () => {
        if (!this._activeSession) {
          return;
        }
        // JupyterLab's built-in command - spawns a fresh pty with the user's
        // shell at the given cwd. The cwd argument is interpreted by the
        // server as a path *relative to the contents manager root*, not an
        // absolute filesystem path - so we translate via _pathUnderRoot,
        // matching the Show in File Browser handling. No kimi, no waiter
        // wrapper, no reuse; for when the user wants a plain shell at the
        // project folder.
        const rel = this._pathUnderRoot(this._activeSession.project_path);
        if (rel === null) {
          // Unreachable - the isEnabled gate above is the user-facing surface
          // and the menu is the only caller. Kept for the type narrowing.
          return;
        }
        this._app.commands
          .execute('terminal:create-new', { cwd: rel })
          .catch(err => this._showError(err));
      }
    });

    this._commands.addCommand('kimi-code-sessions:show-in-filebrowser', {
      label: 'Show in File Browser',
      icon: folderIcon,
      // The file browser can only navigate within the JupyterLab root -
      // grey the item out for projects outside it.
      isEnabled: () =>
        !!this._activeSession &&
        this._pathUnderRoot(this._activeSession.project_path) !== null,
      execute: () => {
        if (!this._activeSession) {
          return;
        }
        const rel = this._pathUnderRoot(this._activeSession.project_path);
        if (rel === null) {
          // Unreachable - the isEnabled gate above is the user-facing surface
          // and the menu is the only caller. Kept for the type narrowing.
          return;
        }
        // JL's built-in command navigates the default file browser to the
        // path and reveals the browser panel.
        this._app.commands
          .execute('filebrowser:go-to-path', { path: rel })
          .catch(err => this._showError(err));
      }
    });

    this._commands.addCommand('kimi-code-sessions:copy-path', {
      label: 'Copy Path',
      execute: () => {
        if (!this._activeSession) {
          return;
        }
        const path = this._activeSession.project_path;
        Clipboard.copyToSystem(path);
      }
    });

    this._commands.addCommand('kimi-code-sessions:copy-session-id', {
      label: 'Copy Session ID',
      execute: () => {
        const id = this._activeSession?.session_id;
        if (id) {
          Clipboard.copyToSystem(id);
        }
      }
    });

    this._commands.addCommand('kimi-code-sessions:cleanup-parallel', {
      label: () =>
        `Clean Up Parallel Sessions (${this._activeSession?.extra_sessions ?? 0})`,
      isVisible: () => (this._activeSession?.extra_sessions ?? 0) > 0,
      execute: () => {
        if (this._activeSession) {
          void this._cleanupParallel(this._activeSession);
        }
      }
    });

    this._commands.addCommand('kimi-code-sessions:switch-branch', {
      label: args => String(args.label ?? ''),
      execute: args => {
        const sessionId = String(args.session_id ?? '');
        if (sessionId) {
          void this._switchBranch(sessionId);
        }
      }
    });

    this._commands.addCommand('kimi-code-sessions:switch-branch-more', {
      label: () => `Manage Sessions... (${this._lastBranches.length})`,
      execute: () => {
        void this._showBranchPopup(
          this._lastBranches,
          this._lastBranchesCurrent
        );
      }
    });

    this._commands.addCommand('kimi-code-sessions:open-branch', {
      label: args => String(args.label ?? ''),
      icon: terminalIcon,
      execute: args => {
        const sessionId = String(args.session_id ?? '');
        if (sessionId) {
          void this._openBranch(sessionId);
        }
      }
    });

    this._commands.addCommand('kimi-code-sessions:branch-session', {
      label: 'Normal',
      execute: () => void this._branchSession(false)
    });

    this._commands.addCommand('kimi-code-sessions:branch-session-yolo', {
      label: 'YOLO',
      icon: shieldIcon,
      execute: () => void this._branchSession(true)
    });

    this._commands.addCommand('kimi-code-sessions:remove', {
      label: 'Remove from Kimi',
      icon: removeIcon,
      execute: () => {
        if (this._activeSession) {
          void this._remove(this._activeSession);
        }
      }
    });

    this._commands.addCommand('kimi-code-sessions:new-session', {
      label: 'New Kimi Session',
      execute: () => void this._newSession(false)
    });

    this._commands.addCommand('kimi-code-sessions:new-session-yolo', {
      label: 'New Kimi Session (YOLO)',
      icon: shieldIcon,
      execute: () => void this._newSession(true)
    });

    // Dropdown for the header's plus button - same command registry and
    // styling as the row context menu.
    this._newSessionMenu = new Menu({ commands: this._commands });
    this._newSessionMenu.addClass('jp-KimiSessionsContextMenu');
    this._newSessionMenu.addItem({
      command: 'kimi-code-sessions:new-session'
    });
    this._newSessionMenu.addItem({
      command: 'kimi-code-sessions:new-session-yolo'
    });

    // Submenu listing the project's other conversations ("branches") -
    // items are rebuilt on every context-menu open from a fresh
    // sessions/branches fetch.
    this._branchSubmenu = new Menu({ commands: this._commands });
    this._branchSubmenu.addClass('jp-KimiSessionsContextMenu');
    this._branchSubmenu.title.label = 'Switch and Manage Sessions';
    this._branchSubmenu.title.icon = switchIcon;

    // Submenu that OPENS a conversation directly in its own terminal (vs the
    // switch submenu, which only changes which branch the row points at).
    // Several branches can be open at once, independently.
    this._openBranchSubmenu = new Menu({ commands: this._commands });
    this._openBranchSubmenu.addClass('jp-KimiSessionsContextMenu');
    this._openBranchSubmenu.title.label = 'Open Branched Conversation';
    this._openBranchSubmenu.title.icon = terminalIcon;

    // Submenu grouping the two branch-session launch modes.
    this._branchSessionMenu = new Menu({ commands: this._commands });
    this._branchSessionMenu.addClass('jp-KimiSessionsContextMenu');
    this._branchSessionMenu.title.label = 'Branch Session';
    this._branchSessionMenu.title.icon = branchIcon;
    this._branchSessionMenu.addItem({
      command: 'kimi-code-sessions:branch-session'
    });
    this._branchSessionMenu.addItem({
      command: 'kimi-code-sessions:branch-session-yolo'
    });

    this._contextMenu = new Menu({ commands: this._commands });
    this._contextMenu.addClass('jp-KimiSessionsContextMenu');
    this._rebuildContextMenu(false);

    this._contextMenu.aboutToClose.connect(() => {
      // Only clear the visual highlight - DO NOT null _activeSession.
      // Lumino fires aboutToClose BEFORE the activated item's command runs,
      // so the command callback still needs to read _activeSession. The
      // field is overwritten on the next contextmenu open.
      this._setActiveRow(null);
    });
  }

  /** Rebuild the context menu's items. Lumino submenu-type items have no
   * ``isVisible`` hook, so the menu is rebuilt per open and the branch
   * submenu inserted only when the row actually has branches. */
  private _rebuildContextMenu(withBranches: boolean): void {
    this._contextMenu.clearItems();
    this._contextMenu.addItem({ command: 'kimi-code-sessions:resume' });
    this._contextMenu.addItem({
      command: 'kimi-code-sessions:resume-yolo'
    });
    this._contextMenu.addItem({
      command: 'kimi-code-sessions:open-terminal'
    });
    this._contextMenu.addItem({
      command: 'kimi-code-sessions:show-in-filebrowser'
    });
    this._contextMenu.addItem({
      command: 'kimi-code-sessions:toggle-favourite'
    });
    this._contextMenu.addItem({ command: 'kimi-code-sessions:copy-path' });
    this._contextMenu.addItem({
      command: 'kimi-code-sessions:copy-session-id'
    });
    this._contextMenu.addItem({ type: 'separator' });
    if (withBranches) {
      this._contextMenu.addItem({
        type: 'submenu',
        submenu: this._openBranchSubmenu
      });
      this._contextMenu.addItem({
        type: 'submenu',
        submenu: this._branchSubmenu
      });
    }
    this._contextMenu.addItem({
      type: 'submenu',
      submenu: this._branchSessionMenu
    });
    this._contextMenu.addItem({
      command: 'kimi-code-sessions:cleanup-parallel'
    });
    this._contextMenu.addItem({ command: 'kimi-code-sessions:remove' });
  }

  /** Open the row context menu, populating the branch submenu first when
   * the project has more than one conversation. On a fetch failure the
   * menu opens without the submenu. */
  private async _openContextMenu(
    session: ISession,
    x: number,
    y: number
  ): Promise<void> {
    let hasBranches = false;
    if (session.extra_sessions > 0) {
      try {
        const data = await requestAPI<IBranchesResponse>(
          `sessions/branches?encoded_path=${encodeURIComponent(session.encoded_path)}`,
          this._serverSettings,
          { cache: 'no-store' }
        );
        this._lastBranches = data.branches;
        this._lastBranchesCurrent = data.current;
        this._branchSubmenu.clearItems();
        this._branchSubmenu.title.label = `Switch and Manage Sessions (${data.branches.length})`;
        // The submenu shows only the 5 most recent inline (fewest clicks
        // for often-used sessions); the full list plus management lives
        // behind the always-present "Manage Sessions..." popup.
        for (const b of data.branches.slice(0, 5)) {
          this._branchSubmenu.addItem({
            command: 'kimi-code-sessions:switch-branch',
            args: {
              session_id: b.session_id,
              label: `${this._branchDisplayName(b)} - ${this._formatRelativeTime(b.file_mtime)}`
            }
          });
        }
        this._branchSubmenu.addItem({ type: 'separator' });
        this._branchSubmenu.addItem({
          command: 'kimi-code-sessions:switch-branch-more'
        });

        // Open submenu: same top-5 branches, but each launches its own
        // terminal directly. Falls through to the Manage Sessions popup for
        // the full list (from which any conversation can also be opened).
        this._openBranchSubmenu.clearItems();
        this._openBranchSubmenu.title.label = `Open Branched Conversation (${data.branches.length})`;
        for (const b of data.branches.slice(0, 5)) {
          this._openBranchSubmenu.addItem({
            command: 'kimi-code-sessions:open-branch',
            args: {
              session_id: b.session_id,
              label: `${this._branchDisplayName(b)} - ${this._formatRelativeTime(b.file_mtime)}`
            }
          });
        }
        this._openBranchSubmenu.addItem({ type: 'separator' });
        this._openBranchSubmenu.addItem({
          command: 'kimi-code-sessions:switch-branch-more'
        });
        hasBranches = data.branches.length > 0;
      } catch {
        hasBranches = false;
      }
    }
    this._rebuildContextMenu(hasBranches);
    this._contextMenu.open(x, y);
  }

  /** A compact copy button for a popup row that copies the given session id
   * to the system clipboard. ``stopPropagation`` keeps the click from
   * switching or selecting the row it sits in. */
  private _branchCopyButton(sessionId: string): HTMLButtonElement {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'jp-KimiSessionsPanel-branchCopy';
    btn.title = 'Copy session id';
    copyIcon.element({ container: btn });
    btn.addEventListener('click', e => {
      e.stopPropagation();
      Clipboard.copyToSystem(sessionId);
    });
    return btn;
  }

  /** Popup with the project's full branch list - browse, filter, switch
   * and manage. Clicking an entry switches while nothing is selected;
   * checkbox selection (one, many, or select-all) arms a two-step Delete
   * button that removes the chosen sessions. The current conversation is
   * shown first, badged and untouchable. */
  private _showBranchPopup(branches: IBranch[], current: string): void {
    // Local working copy so deletions can refresh the list in place.
    let items = [...branches];
    const selected = new Set<string>();
    let deleting = false; // guards the async delete against double-invocation

    const body = document.createElement('div');
    body.className = 'jp-KimiSessionsPanel-branchPopup';

    const search = document.createElement('input');
    search.type = 'search';
    search.placeholder = 'Filter sessions...';
    search.className = 'jp-KimiSessionsPanel-branchSearch';
    body.appendChild(search);

    // Table header strip: select-all on the left, conversation count right.
    const header = document.createElement('div');
    header.className = 'jp-KimiSessionsPanel-branchHeader';
    const selectAllBar = document.createElement('label');
    selectAllBar.className = 'jp-KimiSessionsPanel-branchSelectAll';
    const selectAll = document.createElement('input');
    selectAll.type = 'checkbox';
    selectAllBar.appendChild(selectAll);
    selectAllBar.appendChild(document.createTextNode('Select all'));
    header.appendChild(selectAllBar);
    const countEl = document.createElement('span');
    countEl.className = 'jp-KimiSessionsPanel-branchHeaderCount';
    header.appendChild(countEl);
    body.appendChild(header);

    const list = document.createElement('div');
    list.className = 'jp-KimiSessionsPanel-branchList';
    // role=group makes the aria-label apply to the conversation list region.
    list.setAttribute('role', 'group');
    list.setAttribute('aria-label', 'Conversations');
    body.appendChild(list);

    const footer = document.createElement('div');
    footer.className = 'jp-KimiSessionsPanel-branchFooter';
    // Plain visual counter (selection or last action). The screen-reader
    // announcement lives in a separate live region (srLive) so per-checkbox
    // ticks are not announced on top of the native checkbox.
    const selCount = document.createElement('span');
    selCount.className = 'jp-KimiSessionsPanel-branchSelCount';
    footer.appendChild(selCount);
    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'jp-KimiSessionsPanel-branchDelete';
    deleteBtn.title =
      "Deleted conversations move to the trash when JupyterLab's " +
      'move-to-trash setting is on';
    footer.appendChild(deleteBtn);
    body.appendChild(footer);

    // Visually-hidden polite live region, announced only on delete.
    const srLive = document.createElement('div');
    srLive.className = 'jp-KimiSessionsPanel-srOnly';
    srLive.setAttribute('role', 'status');
    srLive.setAttribute('aria-live', 'polite');
    body.appendChild(srLive);

    const bodyWidget = new Widget({ node: body });
    const dialog = new Dialog({
      title: 'Manage Sessions',
      body: bodyWidget,
      buttons: [Dialog.cancelButton()]
    });

    // Per-row "Open" launches that conversation in its own terminal (via
    // _openBranch, reusing only a terminal already running it) and closes the
    // popup. stopPropagation keeps the click from toggling selection or
    // switching the row.
    const openButton = (sessionId: string): HTMLButtonElement => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'jp-KimiSessionsPanel-branchOpen';
      btn.textContent = 'Open';
      btn.title = 'Open this conversation in its own terminal';
      btn.addEventListener('click', e => {
        e.stopPropagation();
        dialog.dispose();
        void this._openBranch(sessionId);
      });
      return btn;
    };

    const visibleMatches = (): IBranch[] => {
      const needle = search.value.trim().toLowerCase();
      return items.filter(
        b =>
          !needle ||
          b.label.toLowerCase().includes(needle) ||
          b.session_id.toLowerCase().includes(needle)
      );
    };

    const updateControls = () => {
      deleteBtn.disabled = deleting || selected.size === 0;
      deleteBtn.textContent = deleting
        ? 'Deleting...'
        : `Delete (${selected.size})`;
      selCount.textContent = selected.size ? `${selected.size} selected` : '';
      selCount.classList.remove('jp-mod-deleted');
      const visible = visibleMatches();
      const total = items.length + 1; // + the pinned current conversation
      const shown = visible.length + 1; // the current row is always shown
      countEl.textContent = search.value.trim()
        ? `${shown} of ${total}`
        : `${total} conversation${total === 1 ? '' : 's'}`;
      const visibleSelected = visible.filter(b =>
        selected.has(b.session_id)
      ).length;
      selectAll.checked =
        visible.length > 0 && visibleSelected === visible.length;
      selectAll.indeterminate =
        visibleSelected > 0 && visibleSelected < visible.length;
    };

    const render = () => {
      list.replaceChildren();

      // The current conversation leads the list - badged, unselectable,
      // undeletable; only the extras below it are manageable.
      const currentRow = document.createElement('div');
      currentRow.className = 'jp-KimiSessionsPanel-branchRow jp-mod-current';
      currentRow.title = `Session id: ${current}`;
      // Expose the active state to assistive tech - the brand left-bar, tint
      // and the plain "current" text are visual-only cues.
      currentRow.setAttribute('aria-current', 'true');
      // Empty select cell keeps the name column aligned with branch rows.
      const currentSelect = document.createElement('span');
      currentSelect.className = 'jp-KimiSessionsPanel-branchSelectCell';
      currentRow.appendChild(currentSelect);
      const currentLabel = document.createElement('span');
      currentLabel.className = 'jp-KimiSessionsPanel-branchLabel';
      const currentName = this._activeSession
        ? this._lookupName(this._activeSession)
        : this._shortSessionId(current);
      currentLabel.textContent = `${currentName} (${this._shortSessionId(current)})`;
      currentRow.appendChild(currentLabel);
      const badge = document.createElement('span');
      badge.className = 'jp-KimiSessionsPanel-branchCurrentBadge';
      badge.textContent = 'current';
      currentRow.appendChild(badge);
      currentRow.appendChild(openButton(current));
      currentRow.appendChild(this._branchCopyButton(current));
      list.appendChild(currentRow);

      const matches = visibleMatches();
      if (matches.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'jp-KimiSessionsPanel-emptySection';
        empty.textContent = items.length
          ? 'No matching sessions.'
          : 'No other conversations.';
        list.appendChild(empty);
        return;
      }
      for (const b of matches) {
        const row = document.createElement('div');
        row.className = 'jp-KimiSessionsPanel-branchRow';
        row.title = `Session id: ${b.session_id}`;

        const selectCell = document.createElement('span');
        selectCell.className = 'jp-KimiSessionsPanel-branchSelectCell';
        const check = document.createElement('input');
        check.type = 'checkbox';
        check.checked = selected.has(b.session_id);
        check.setAttribute(
          'aria-label',
          `Select ${this._branchDisplayName(b)}`
        );
        // The checkbox is its own click zone - ticking must not switch.
        check.addEventListener('click', e => {
          e.stopPropagation();
          if (deleting) {
            // Keyboard Space isn't blocked by the busy scrim's pointer-events;
            // re-sync the native toggle on the next render.
            check.checked = selected.has(b.session_id);
            return;
          }
          if (check.checked) {
            selected.add(b.session_id);
          } else {
            selected.delete(b.session_id);
          }
          updateControls();
        });
        selectCell.appendChild(check);
        // The whole cell is a select target (>=24px), not just the checkbox;
        // clicking the padding toggles via the checkbox's own handler.
        selectCell.addEventListener('click', e => {
          if (e.target !== check) {
            e.stopPropagation();
            check.click();
          }
        });
        row.appendChild(selectCell);

        const label = document.createElement('span');
        label.className = 'jp-KimiSessionsPanel-branchLabel';
        label.textContent = this._branchDisplayName(b);
        row.appendChild(label);

        const time = document.createElement('span');
        time.className = 'jp-KimiSessionsPanel-branchTime';
        time.textContent = this._formatRelativeTime(b.file_mtime);
        row.appendChild(time);

        row.appendChild(openButton(b.session_id));
        row.appendChild(this._branchCopyButton(b.session_id));

        row.addEventListener('click', () => {
          // Selection mode: while anything is ticked, row clicks toggle
          // selection - no accidental switch mid-selection.
          if (selected.size > 0) {
            if (selected.has(b.session_id)) {
              selected.delete(b.session_id);
            } else {
              selected.add(b.session_id);
            }
            check.checked = selected.has(b.session_id);
            updateControls();
            return;
          }
          dialog.dispose();
          void this._switchBranch(b.session_id);
        });
        list.appendChild(row);
      }
    };

    selectAll.addEventListener('change', () => {
      if (deleting) {
        return;
      }
      // Select-all acts on the visible (filtered) rows only.
      const visible = visibleMatches();
      if (selectAll.checked) {
        visible.forEach(b => selected.add(b.session_id));
      } else {
        visible.forEach(b => selected.delete(b.session_id));
      }
      render();
      updateControls();
    });

    // Busy-lock the whole body (button + list) during the async delete so a
    // slow backend cannot be double-clicked into deleting the same set twice
    // and a mid-flight selection cannot be silently discarded.
    const setDeleting = (on: boolean) => {
      deleting = on;
      // Scrim the whole popup body (search, select-all, list) so nothing can
      // be re-ticked or re-triggered mid-flight; the Dialog's Cancel button
      // sits outside the body and stays usable. aria-busy goes on the live
      // list region, not the (disabled, unannounced) button.
      body.classList.toggle('jp-mod-busy', on);
      if (on) {
        list.setAttribute('aria-busy', 'true');
      } else {
        list.removeAttribute('aria-busy');
      }
    };

    deleteBtn.addEventListener('click', () => {
      if (deleting || selected.size === 0) {
        return;
      }
      // No confirmation here: deletions move to trash when JupyterLab's
      // move-to-trash setting is on (permanent otherwise), and a
      // second Lumino dialog stacked on this popup renders detached. The only
      // confirmed destructive actions are Clean Up Parallel Sessions and
      // Remove from Kimi (whole-project / bulk). Feedback is given instead
      // of a prompt: a live "N deleted" status (a failure still toasts
      // via _deleteBranches).
      setDeleting(true);
      updateControls();
      void this._deleteBranches([...selected])
        .then(async deleted => {
          if (deleted === null) {
            setDeleting(false);
            updateControls(); // re-enable; selection unchanged
            return;
          }
          // Re-sync from disk truth, not an optimistic splice: a branch the
          // backend skipped (it became the resolved-current, or was already
          // gone) must not vanish from the list while it still exists.
          const session = this._activeSession;
          try {
            if (!session) {
              throw new Error('no active session');
            }
            const fresh = await requestAPI<IBranchesResponse>(
              `sessions/branches?encoded_path=${encodeURIComponent(session.encoded_path)}`,
              this._serverSettings,
              { cache: 'no-store' }
            );
            items = fresh.branches;
          } catch {
            // Refetch failed (rare): optimistic removal. The announced count
            // may then differ from rows removed if the backend skipped one;
            // the panel's own _fetch (in _deleteBranches) reconciles on disk.
            items = items.filter(b => !selected.has(b.session_id));
          }
          // The user may have closed the popup during the refetch await - don't
          // touch shared state or a detached DOM in that case.
          if (!bodyWidget.isAttached) {
            return;
          }
          setDeleting(false);
          selected.clear();
          this._lastBranches = items;
          render();
          updateControls();
          // Feedback (no prompt): a perceivable counter + a polite SR
          // announcement of the actual number the backend removed. The
          // disposition (trash vs permanent) follows JupyterLab's
          // move-to-trash setting, which this side cannot observe - so the
          // status claims only the count, and the button's tooltip carries
          // the hedged explanation.
          const removed = `${deleted} deleted`;
          selCount.textContent = removed;
          selCount.classList.add('jp-mod-deleted');
          // Clear then re-set on the next tick so an identical count (two
          // single deletes in a row) is still re-announced by aria-live.
          srLive.textContent = '';
          window.setTimeout(() => {
            srLive.textContent = removed;
          }, 60);
          // Keep focus inside the dialog (render() destroyed the focused row).
          // Focus the select-all checkbox (a real control with a reliable
          // keyboard ring), unless the user is still typing in the search box.
          if (document.activeElement !== search) {
            selectAll.focus();
          }
        })
        .catch(() => {
          // Defensive: never leave the button stuck disabled if the chain
          // rejects unexpectedly.
          if (bodyWidget.isAttached) {
            setDeleting(false);
            updateControls();
          }
        });
    });

    search.addEventListener('input', () => {
      if (deleting) {
        return;
      }
      render();
      updateControls();
    });
    render();
    updateControls();

    // dialog.dispose() (on open/switch) rejects this promise with `undefined`;
    // catch it so the teardown never surfaces as an unhandled promise rejection.
    dialog.launch().catch(() => undefined);
    search.focus();
  }

  /** Delete the given branch sessions of the active row's project.
   * Returns the removed count, or null on failure (after notifying).
   * Always resyncs the panel so the row's conversation count drops. */
  private async _deleteBranches(sessionIds: string[]): Promise<number | null> {
    const session = this._activeSession;
    if (!session) {
      return null;
    }
    const body: IDeleteBranchesRequest = {
      encoded_path: session.encoded_path,
      session_ids: sessionIds
    };
    try {
      const result = await requestAPI<IDeleteBranchesResponse>(
        'sessions/delete-branches',
        this._serverSettings,
        { method: 'POST', body: JSON.stringify(body) }
      );
      return result.removed_count;
    } catch (err) {
      Notification.error(`Delete failed: ${String(err)}`, {
        autoClose: 4000
      });
      return null;
    } finally {
      await this._fetch();
    }
  }

  /** Fork the active row's current conversation into a new named branch.
   *
   * Asks for a name, then POSTs ``sessions/fork`` - the server copies the
   * session directory synchronously (new ``session_<uuid>``, index line
   * appended, title stamped, fork pinned as the project's current
   * conversation) and returns the new id. The fork therefore exists the
   * moment the POST resolves, so no lazy-file watcher is needed: we launch
   * a terminal resuming the fork (``kimi -S <new id>``) and refresh. The
   * terminal is tagged with the fork's id, so a later click on the
   * now-current forked row reuses this terminal.
   */
  private async _branchSession(forceYolo: boolean): Promise<void> {
    const session = this._activeSession;
    if (!session) {
      return;
    }
    const named = await InputDialog.getText({
      title: 'Branch Session',
      label: 'Name for the new session',
      placeholder: this._lookupName(session)
    });
    if (!named.button.accept || !named.value || !named.value.trim()) {
      return;
    }
    const title = named.value.trim();
    const spinner = this._showLaunchSpinner();
    try {
      const forkBody: IForkRequest = {
        encoded_path: session.encoded_path,
        session_id: session.session_id,
        name: title
      };
      const fork = await requestAPI<IForkResponse>(
        'sessions/fork',
        this._serverSettings,
        { method: 'POST', body: JSON.stringify(forkBody) }
      );
      const launchBody: ILaunchTerminalRequest = {
        project_path: session.project_path,
        session_id: fork.session_id,
        yolo: forceYolo || this._yoloMode
      };
      const launched = await requestAPI<ILaunchTerminalResponse>(
        'launch-terminal',
        this._serverSettings,
        { method: 'POST', body: JSON.stringify(launchBody) }
      );
      const widget: any = await this._app.commands.execute('terminal:open', {
        name: launched.terminal_name
      });
      if (widget?.id) {
        // The terminal runs the FORK, so tag it with that id - a
        // later click on the now-current forked row reuses this terminal.
        this._terminalsByPath.set(session.project_path, {
          widget,
          sessionId: fork.session_id
        });
        this._wireTerminalDisposal(session.project_path, widget);
        this._focusTerminal(widget);
      }
    } catch (err) {
      this._showError(err);
      return;
    } finally {
      spinner.dispose();
    }
    // The fork exists on disk already (the POST is synchronous), so one
    // refresh surfaces the new branch immediately - no watcher needed.
    await this._fetch().catch(() => {
      /* a poll tick will retry; nothing actionable here */
    });
  }

  /** Switch the active row's project to another conversation branch.
   * The backend pins the chosen conversation as current; a refresh then
   * shows the selected conversation as the row's current one. */
  private async _switchBranch(sessionId: string): Promise<void> {
    const session = this._activeSession;
    if (!session) {
      return;
    }
    const body: ISwitchRequest = {
      encoded_path: session.encoded_path,
      session_id: sessionId
    };
    try {
      const result = await requestAPI<ISwitchResponse>(
        'sessions/switch',
        this._serverSettings,
        { method: 'POST', body: JSON.stringify(body) }
      );
      if (result.current !== result.requested) {
        // The server could not pin the requested conversation as current.
        Notification.warning(
          'The conversation could not be made current - it may have been ' +
            'removed, or the current pin could not be written.',
          { autoClose: 4000 }
        );
      }
    } catch (err) {
      const notFound =
        err instanceof ServerConnection.ResponseError &&
        err.response.status === 404;
      Notification.error(
        notFound
          ? 'Branch no longer exists - the session list has been refreshed.'
          : `Branch switch failed: ${err}`,
        { autoClose: 4000 }
      );
    } finally {
      // Best-effort refresh - never let a transient fetch failure reject this
      // fire-and-forget switch (its callers do not await it).
      await this._fetch().catch(() => undefined);
    }
  }

  /** Open a specific conversation branch in its own terminal.
   *
   * Reuse only a terminal already running THIS conversation; otherwise launch
   * a fresh ``kimi -S <id>``. So several branches of one project can
   * be open independently and side by side - opening branch B never disturbs
   * branch A's terminal, and never refocuses a terminal running a different
   * conversation. Honours the global yolo toggle like a normal resume.
   *
   * Naming the conversation is all this owes the server: it builds the
   * resume argv per conversation id at launch, so this path needs no state
   * of its own - including for the popup's pinned current row, which is the
   * row's own conversation. */
  private async _openBranch(sessionId: string): Promise<void> {
    const active = this._activeSession;
    if (!active || !sessionId) {
      return;
    }
    await this._resumeInTerminal({ ...active, session_id: sessionId });
  }

  // --------------------------------------------------------------- polling

  private _startPolling(): void {
    if (this._pollHandle !== null) {
      return;
    }
    this._pollHandle = window.setInterval(() => {
      // Don't reshuffle rows while the user is interacting with the context menu
      if (this._contextMenu.isAttached) {
        return;
      }
      // Defer the tick while the pointer is inside the panel body - a poll
      // re-render can re-sort rows under the cursor mid-click. The body's
      // mouseleave handler flushes it as soon as the pointer leaves, so a
      // parked cursor delays the refresh rather than freezing the list.
      if (this._bodyEl.matches(':hover')) {
        this._pendingRefresh = true;
        return;
      }
      // Console-only: a transient poll failure must not toast every 30 s.
      this._fetch().catch(err =>
        console.error('[jupyterlab_kimi_code_extension]', err)
      );
    }, POLL_INTERVAL_MS);
  }

  private _stopPolling(): void {
    if (this._pollHandle !== null) {
      window.clearInterval(this._pollHandle);
      this._pollHandle = null;
    }
  }

  private readonly _app: JupyterFrontEnd;
  private readonly _serverSettings: ServerConnection.ISettings;
  private _bodyEl!: HTMLDivElement;
  private _loadingEl: HTMLElement | null = null;
  private _refreshBtn: HTMLButtonElement | null = null;
  private _filterBtn: HTMLButtonElement | null = null;
  private _searchEl: HTMLInputElement | null = null;
  private _searchWrapEl: HTMLDivElement | null = null;
  private _searchClearEl: HTMLButtonElement | null = null;
  private _sessions: ISession[] | null = null;
  private _expanded: Record<SectionKey, boolean> = loadExpanded();
  private _commands!: CommandRegistry;
  private _contextMenu!: Menu;
  private _branchSubmenu!: Menu;
  private _openBranchSubmenu!: Menu;
  private _branchSessionMenu!: Menu;
  private _lastBranches: IBranch[] = [];
  private _lastBranchesCurrent = '';
  private _newSessionMenu!: Menu;
  private _activeSession: ISession | null = null;
  private _activeRowEl: HTMLElement | null = null;
  private _pollHandle: number | null = null;
  // A poll tick the hover guard deferred; flushed on the body's mouseleave.
  private _pendingRefresh = false;
  private readonly _removingPaths: Set<string> = new Set();
  private readonly _terminalTracker: ITerminalTracker | null;
  private readonly _fileBrowser: IDefaultFileBrowser | null;
  private readonly _colourfulTabs: IColourfulTabs | null;
  // Microcache of the most-recent terminal per project, tagged with the
  // conversation it is running so reuse can tell a project's branches apart.
  private readonly _terminalsByPath: Map<
    string,
    { widget: any; sessionId?: string }
  > = new Map();
  // In-flight launches, keyed per CONVERSATION (path + session id) so two
  // different branches of one project can open independently and concurrently.
  private readonly _pendingByPath: Map<string, Promise<void>> = new Map();
  private readonly _rootDir: string;
  private _presentationMode: PresentationMode = DEFAULT_PRESENTATION_MODE;
  private _recentLimit: number = DEFAULT_RECENT_LIMIT;
  private _yoloMode: boolean = false;
  private _colouredTabs: boolean = true;
  private _displayNames: Map<string, string> = new Map();
  private _filter: string = '';
}
