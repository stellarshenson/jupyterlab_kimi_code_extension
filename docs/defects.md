# Defects - jupyterlab_kimi_code_extension

`[ ]` open, `[x]` fixed. Dated notes under each track how it evolved.

## Fork

- [ ] `DEF-1` **fork-by-copy coupled to kimi 0.31.0 session-dir format** - LOW; fork_session copies `session_<uuid>/` and appends `session_index.jsonl`, both internal layouts; cause: kimi has no fork CLI flag so the extension owns fork mechanics; fix: re-verify against live `~/.kimi-code` on every kimi upgrade, adapt `fork_session` when the layout moves; `jupyterlab_kimi_code_extension/sessions.py`
  - 2026-07-31 reported: design risk recorded at implementation time (verified against kimi 0.31.0), see [acc-crit Documented Deviations](acc-crit-jupyterlab_kimi_code_extension.md#documented-deviations)

## Server

- [x] `DEF-2` **untitled branch labels all rendered as the constant 'session\_'** - list_branches fallback label truncated session_<uuid> to its first 8 chars which is always the literal prefix; cause: reference used 8-char claude uuids, kimi ids carry a session_ prefix; fix: label from uuid part name[8:16]; `jupyterlab_kimi_code_extension/sessions.py`
  - 2026-07-31 reported: found by pytest port agent during suite authoring
  - 2026-07-31 fixed: fixed: name[8:16] fallback, pinned by test_list_branches_untitled_label_is_short_uuid, 144 pytest green
- [x] `DEF-3` **fork endpoint accepted unknown body keys** - SessionForkHandler ignored extras while launch-terminal rejects them; cause: _ALLOWED_KEYS gate only ported to launch handler; fix: same gate on fork; `jupyterlab_kimi_code_extension/routes.py`
  - 2026-07-31 reported: found by pytest port agent during suite authoring
  - 2026-07-31 fixed: fixed: _ALLOWED_KEYS gate on fork, pinned by test_fork_endpoint_rejects_unknown_keys, 144 pytest green

## Panel

- [x] `DEF-4` **frontend short ids rendered the constant 'session\_' prefix** - branch submenu and Manage Sessions current-row sliced session_id from the front; cause: frontend twin of DEF-2, claude ids were bare uuids; fix: _shortSessionId helper slicing the uuid part (8,16); `src/widget.ts`
  - 2026-07-31 reported: found by Galata port agent during ui-test authoring
  - 2026-07-31 fixed: fixed: _shortSessionId helper, pinned by jest short-id contract test, 51 jest green + tsc clean
- [ ] `DEF-5` **message-count cache never evicts entries for deleted sessions** - LOW; _message_count_cache keys survive delete_branches/cleanup/remove, so a long-lived server retains one small tuple per wire file ever seen; cause: cache added in round-1 review to stop re-scanning multi-MB transcripts every 30s poll; fix: declined - keys are uuid4 paths that never recur and cannot alias, pruning means touching three delete paths for tens of bytes; `jupyterlab_kimi_code_extension/sessions.py`
  - 2026-07-31 reported: raised by round-2 adversarial review (slop-hunter, ux-designer); declined as machinery exceeding the defect
- [ ] `DEF-6` **each session row is its own tab stop** - LOW; all three sections expand by default and one project appears in Favorites, Recent and All, so a large list costs many Tab presses and announces the same name three times; cause: round-1 accessibility fix made every row focusable; fix: deferred - APG roving-tabindex with Up/Down navigation is the correct pattern but is a new interaction model, not a review remedy; `src/widget.ts`
  - 2026-07-31 reported: raised by round-2 adversarial review (ux-designer); deferred to a dedicated accessibility pass
