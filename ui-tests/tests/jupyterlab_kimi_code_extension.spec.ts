import { expect, test } from '@jupyterlab/galata';

/**
 * Don't load JupyterLab webpage before running the tests.
 * This is required to ensure we capture all log messages.
 */
test.use({ autoGoto: false });

/**
 * Build-agnostic readiness check.
 *
 * Galata's default `waitForApplication` calls `isInSimpleMode()`, which waits
 * on the status-bar single-document-mode toggle (`getByRole('switch', { name:
 * 'Simple' })`). Some JupyterLab builds do not render that toggle, so the
 * default check hangs and every test times out at `page.goto()`. Wait on the
 * splash teardown plus the lab shell instead - present in every build - so the
 * suite is robust to the toggle's absence.
 */
test.use({
  waitForApplication: async ({ baseURL }, use) => {
    void baseURL;
    const waitIsReady = async (page: any): Promise<void> => {
      await page.locator('#jupyterlab-splash').waitFor({ state: 'detached' });
      await page.locator('.jp-LabShell').first().waitFor({ state: 'visible' });
    };
    await use(waitIsReady);
  }
});

const PANEL_ID = 'jupyterlab-kimi-code-extension';

/** Server-side terminal count - the ground truth a "terminal opened" or
 * "terminal reused" claim must be checked against (the widget alone can
 * be visible while no pty exists). */
async function terminalCount(page: any): Promise<number> {
  const response = await page.request.get('/api/terminals');
  expect(response.ok()).toBe(true);
  return ((await response.json()) as unknown[]).length;
}

/** Load the app and reveal the sessions panel in the sidebar. */
async function openPanel(page: any) {
  await page.goto();
  await page.sidebar.openTab(PANEL_ID);
  const panel = page.locator(`#${PANEL_ID}`);
  await expect(panel).toBeVisible();
  return panel;
}

/** The seeded "kimiproj" row (rendered once in Recent and once in All). */
function projectRow(panel: any) {
  return panel
    .locator('.jp-KimiSessionsPanel-row', { hasText: 'kimiproj' })
    .first();
}

/**
 * Hover a submenu item and return the submenu that opens, gated on it
 * actually attaching.
 *
 * `page.locator('.lm-Menu').last()` is NOT enough on its own: until Lumino's
 * open delay elapses (~366ms) only the root menu exists, `.last()` resolves to
 * IT, and its own command items are visible - so `entries.first()` passes
 * against "Resume" and the whole test silently drives the wrong menu. That is
 * not hypothetical: it is how `two different branches open as two independent
 * terminals` passed for months while launching the current session twice.
 */
async function openSubmenu(page: any, menu: any, label: string) {
  await menu.locator('.lm-Menu-itemLabel', { hasText: label }).hover();
  await expect(page.locator('.lm-Menu')).toHaveCount(2, { timeout: 10000 });
  const submenu = page.locator('.lm-Menu').nth(1);
  // Counter-guard: the root menu carries this item, the submenu never does.
  await expect(submenu).not.toContainText('Remove from Kimi');
  return submenu;
}

/** The seeded workspace's encoded_path, read from the server. */
async function currentEncodedPath(page: any): Promise<string> {
  const res = await page.request.get(
    '/jupyterlab-kimi-code-extension/sessions'
  );
  return (await res.json()).sessions[0].encoded_path;
}

/** Record the session_id of every launch-terminal POST the page issues. */
function recordLaunches(page: any): string[] {
  const launched: string[] = [];
  page.on('request', (req: any) => {
    if (req.url().includes('launch-terminal') && req.method() === 'POST') {
      try {
        launched.push(JSON.parse(req.postData() ?? '{}').session_id ?? null);
      } catch {
        launched.push('<unparseable>');
      }
    }
  });
  return launched;
}

/** Right-click the seeded "kimiproj" row and return its context menu. */
async function openRowMenu(page: any) {
  const panel = await openPanel(page);
  const row = projectRow(panel);
  await expect(row).toBeVisible({ timeout: 15000 });
  await row.click({ button: 'right' });
  // The menu opens only after the branches fetch resolves.
  const menu = page.locator('.lm-Menu.jp-KimiSessionsContextMenu').first();
  await expect(menu).toBeVisible({ timeout: 15000 });
  return menu;
}

test('should emit an activation console message', async ({ page }) => {
  const logs: string[] = [];

  page.on('console', message => {
    logs.push(message.text());
  });

  await page.goto();

  // The test server puts a fake `kimi` on PATH, so activation must take the
  // enabled path and log the standard message (the `kimi`-not-found info
  // message would mean the seeded PATH never reached the server). Activation
  // is async (status roundtrip + settings load), so poll rather than snapshot.
  await expect
    .poll(
      () =>
        logs.some(
          s =>
            s ===
            'JupyterLab extension jupyterlab_kimi_code_extension is activated!'
        ),
      { timeout: 15000 }
    )
    .toBe(true);
});

test('panel lists the seeded project under Recent and All, hiding empty Favorites', async ({
  page
}) => {
  const panel = await openPanel(page);

  // Wait for the sessions fetch to render rows before counting sections.
  await expect(panel.locator('.jp-KimiSessionsPanel-row').first()).toBeVisible({
    timeout: 15000
  });

  // No favourites seeded -> the Favorites section is not rendered at all.
  await expect(
    panel.locator('.jp-KimiSessionsPanel-section[data-section="favourites"]')
  ).toHaveCount(0);
  await expect(panel.locator('.jp-KimiSessionsPanel-section')).toHaveCount(2);
  await expect(
    panel.locator('[data-section="recent"] .jp-KimiSessionsPanel-sectionLabel')
  ).toHaveText('Recent (1)');
  await expect(
    panel.locator('[data-section="all"] .jp-KimiSessionsPanel-sectionLabel')
  ).toHaveText('All (1)');

  // One seeded workspace -> exactly one row per section.
  await expect(panel.locator('.jp-KimiSessionsPanel-row')).toHaveCount(2);
  // The seeded project has three conversations -> branch badge "3".
  await expect(
    projectRow(panel).locator('.jp-KimiSessionsPanel-branchBadge')
  ).toHaveText('3');
});

test('plus button opens the new-session menu with exactly two items', async ({
  page
}) => {
  const panel = await openPanel(page);

  await panel
    .locator('button[title="New Kimi session in the current folder"]')
    .click();

  const menu = page.locator('.lm-Menu.jp-KimiSessionsContextMenu');
  await expect(menu).toBeVisible();
  const labels = menu.locator('.lm-Menu-itemLabel');
  await expect(labels).toHaveCount(2);
  await expect(labels.filter({ hasText: /^New Kimi Session$/ })).toHaveCount(1);
  await expect(
    labels.filter({ hasText: /^New Kimi Session \(YOLO\)$/ })
  ).toHaveCount(1);
});

test('new-session menu item opens a terminal in the current folder', async ({
  page
}) => {
  const panel = await openPanel(page);
  const before = await terminalCount(page);

  await panel
    .locator('button[title="New Kimi session in the current folder"]')
    .click();
  const menu = page.locator('.lm-Menu.jp-KimiSessionsContextMenu');
  await menu
    .locator('.lm-Menu-itemLabel', { hasText: /^New Kimi Session$/ })
    .click();

  // The launch flow shows a modal spinner, POSTs launch-terminal (bare
  // `kimi`, the fake script), then attaches JL's terminal widget. xterm
  // paints to canvas so the script's output is not assertable via DOM text;
  // instead confirm the server now reports one more live terminal.
  await expect(page.locator('.jp-Terminal').first()).toBeVisible({
    timeout: 30000
  });
  await expect
    .poll(() => terminalCount(page), { timeout: 15000 })
    .toBe(before + 1);
});

test('row click resumes in a terminal and a second click reuses it', async ({
  page
}) => {
  const panel = await openPanel(page);
  await expect(projectRow(panel)).toBeVisible({ timeout: 15000 });

  const before = await terminalCount(page);
  await projectRow(panel).click();
  await expect(page.locator('.jp-Terminal').first()).toBeVisible({
    timeout: 30000
  });
  await expect
    .poll(() => terminalCount(page), { timeout: 15000 })
    .toBe(before + 1);

  // Clicking the SAME row again must reuse the terminal already running this
  // conversation (focus, not spawn). The panel re-rendered after the launch
  // refresh, so the locator re-resolves to the fresh row element.
  await projectRow(panel).click();
  // Reuse is silent - give a wrongful second launch time to happen, then
  // assert the server-side count is unchanged.
  await page.waitForTimeout(3000);
  expect(await terminalCount(page)).toBe(before + 1);
});

test('context menu offers branch submenus for a multi-conversation project', async ({
  page
}) => {
  const menu = await openRowMenu(page);

  // Two seeded branches beyond the current conversation -> both submenus
  // carry the (2) count in their labels.
  await expect(
    menu.locator('.lm-Menu-itemLabel', {
      hasText: 'Open Branched Conversation (2)'
    })
  ).toBeVisible();
  await expect(
    menu.locator('.lm-Menu-itemLabel', {
      hasText: 'Switch and Manage Sessions (2)'
    })
  ).toBeVisible();
  await expect(
    menu.locator('.lm-Menu-itemLabel', { hasText: 'Branch Session' })
  ).toBeVisible();
});

test('a long conversation title cannot stretch the branch submenu (DEF-18)', async ({
  page
}) => {
  // Session 0 is seeded with a paragraph-length auto-generated title, which
  // is what kimi produces for any session the user has not renamed. Before
  // the cap this rendered an 850px submenu - most of the window - because
  // Lumino sets no max-width on `.lm-Menu-itemLabel`.
  const menu = await openRowMenu(page);
  const submenu = await openSubmenu(page, menu, 'Open Branched Conversation');
  const entries = submenu.locator('.lm-Menu-item[data-type="command"]');
  await expect(entries.first()).toBeVisible({ timeout: 10000 });

  // The long title is cut and marked with an ellipsis...
  const labels = await submenu.locator('.lm-Menu-itemLabel').allInnerTexts();
  const longOne = labels.find(t => t.startsWith('List ONLY the names'));
  expect(longOne).toBeDefined();
  expect(longOne).toContain('…');
  expect(longOne).not.toContain('no other text.');

  // ...while the short id and relative time that tell two branches apart
  // survive the cap.
  expect(longOne).toMatch(/\([0-9a-f]{8}\) - /);

  // The wide-script title is capped too. A character-counting cap left this
  // one untouched (60 Han glyphs = ~2x the width of 60 Latin), so the ellipsis
  // is the assertion that a code-unit cap cannot satisfy.
  const cjkOne = labels.find(t => t.startsWith('请仔细阅读'));
  expect(cjkOne).toBeDefined();
  expect(cjkOne).toContain('…');
  expect(cjkOne).toMatch(/\([0-9a-f]{8}\) - /);

  // And the rendered submenu stays a menu, not a banner across the window.
  // Measured against this fixture: 850px uncapped Latin, 851px uncapped CJK,
  // 495px capped. The bar is a regression guard between those populations,
  // not an aesthetic target - remove the cap and it fails on either script.
  const box = await submenu.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeLessThan(700);
});

test('two different branches open as two independent terminals', async ({
  page
}) => {
  const before = await terminalCount(page);
  const launched = recordLaunches(page);

  // Open the first branch.
  let menu = await openRowMenu(page);
  let entries = (
    await openSubmenu(page, menu, 'Open Branched Conversation')
  ).locator('.lm-Menu-item[data-type="command"]');
  await expect(entries.first()).toBeVisible({ timeout: 10000 });
  await entries.first().click();
  await expect(page.locator('.jp-Terminal').first()).toBeVisible({
    timeout: 30000
  });

  // Open a different branch - it must NOT replace or reuse the first
  // terminal (a branch id never matches the other branch's running id).
  menu = await openRowMenu(page);
  entries = (
    await openSubmenu(page, menu, 'Open Branched Conversation')
  ).locator('.lm-Menu-item[data-type="command"]');
  await expect(entries.nth(1)).toBeVisible({ timeout: 10000 });
  await entries.nth(1).click();
  await expect(page.locator('.jp-Terminal').first()).toBeVisible({
    timeout: 30000
  });

  await expect
    .poll(() => terminalCount(page), { timeout: 15000 })
    .toBeGreaterThanOrEqual(before + 2);

  // Terminal COUNT alone cannot tell a branch launch from two Resumes of the
  // current conversation - which is exactly what this test used to do. Assert
  // on what was actually launched: two distinct ids, neither the current one.
  const branches = await page.request
    .get(
      `/jupyterlab-kimi-code-extension/sessions/branches?encoded_path=${await currentEncodedPath(page)}`
    )
    .then((r: any) => r.json());
  expect(launched).toHaveLength(2);
  expect(new Set(launched).size).toBe(2);
  expect(launched).not.toContain(branches.current);
  for (const id of launched) {
    expect(branches.branches.map((b: any) => b.session_id)).toContain(id);
  }
});

test('Manage Sessions popup exposes per-row Open buttons and dismisses on Open', async ({
  page
}) => {
  const menu = await openRowMenu(page);
  await menu
    .locator('.lm-Menu-itemLabel', { hasText: 'Open Branched Conversation' })
    .hover();
  const submenu = page.locator('.lm-Menu').last();
  // Wait for the submenu to open, then click its "Manage Sessions..."
  // COMMAND item. The `[data-type="command"]` filter is essential -
  // `hasText: 'Manage Sessions'` alone also matches the "Switch and Manage
  // Sessions" submenu PARENT, which does not open the popup.
  await expect(
    submenu.locator('.lm-Menu-item[data-type="command"]').first()
  ).toBeVisible({ timeout: 10000 });
  await submenu
    .locator('.lm-Menu-item[data-type="command"]', {
      hasText: 'Manage Sessions'
    })
    .click();

  const popup = page.locator('.jp-KimiSessionsPanel-branchPopup');
  await expect(popup).toBeVisible({ timeout: 15000 });
  // Every row carries an Open button: the pinned current conversation plus
  // the two seeded branches (the fork test runs after this one).
  const openButtons = popup.locator('.jp-KimiSessionsPanel-branchOpen');
  await expect(openButtons).toHaveCount(3);

  // Opening from the popup launches a terminal and dismisses the popup.
  await openButtons.first().click();
  await expect(page.locator('.jp-Terminal').first()).toBeVisible({
    timeout: 30000
  });
  await expect(popup).toBeHidden();
});

/**
 * Fork flow, end to end: name dialog -> POST sessions/fork (the server
 * copies the session dir under a fresh id, appends the index line, pins the
 * fork as current) -> `kimi -S <new id>` terminal -> panel refresh showing
 * one more conversation. Runs LAST because it permanently grows the seeded
 * workspace from 3 to 4 sessions.
 */
test('Branch Session forks the conversation and increments the branch count', async ({
  page
}) => {
  const panel = await openPanel(page);
  await expect(projectRow(panel)).toBeVisible({ timeout: 15000 });
  await expect(
    projectRow(panel).locator('.jp-KimiSessionsPanel-branchBadge')
  ).toHaveText('3');
  const before = await terminalCount(page);

  await projectRow(panel).click({ button: 'right' });
  const menu = page.locator('.lm-Menu.jp-KimiSessionsContextMenu').first();
  await expect(menu).toBeVisible({ timeout: 15000 });
  await menu
    .locator('.lm-Menu-itemLabel', { hasText: 'Branch Session' })
    .hover();
  const submenu = page.locator('.lm-Menu').last();
  const normalItem = submenu.locator('.lm-Menu-item[data-type="command"]', {
    hasText: 'Normal'
  });
  await expect(normalItem).toBeVisible({ timeout: 10000 });
  await normalItem.click();

  // Name input dialog (InputDialog.getText), accepted with a custom title.
  const dialog = page.locator('.jp-Dialog');
  await expect(dialog).toBeVisible({ timeout: 15000 });
  await dialog.locator('input').fill('Forked in e2e');
  await dialog.locator('.jp-Dialog-button.jp-mod-accept').click();

  // The fork exists on disk the moment the POST resolves; the launch then
  // opens a terminal resuming it.
  await expect(page.locator('.jp-Terminal').first()).toBeVisible({
    timeout: 30000
  });
  await expect
    .poll(() => terminalCount(page), { timeout: 15000 })
    .toBe(before + 1);

  // The panel's post-fork refresh must surface the fork as the workspace's
  // current conversation: the row is relabelled with the fork's custom
  // title, and its badge counts all sessions of the workspace, now 4.
  const forkedRow = panel
    .locator('.jp-KimiSessionsPanel-row', { hasText: 'Forked in e2e' })
    .first();
  await expect(forkedRow).toBeVisible({ timeout: 15000 });
  await expect(
    forkedRow.locator('.jp-KimiSessionsPanel-branchBadge')
  ).toHaveText('4', { timeout: 15000 });
});
