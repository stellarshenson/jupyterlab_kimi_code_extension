import {
  ILabShell,
  ILayoutRestorer,
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { IDefaultFileBrowser } from '@jupyterlab/filebrowser';
import { ISettingRegistry } from '@jupyterlab/settingregistry';
import { ITerminalTracker } from '@jupyterlab/terminal';
import { IColourfulTabs } from 'jupyterlab_colourful_tab_extension';

import { requestAPI } from './request';
import { IStatusResponse } from './types';
import { KimiCodeSessionsWidget } from './widget';

const PLUGIN_ID = 'jupyterlab_kimi_code_extension:plugin';
const WIDGET_ID = 'jupyterlab-kimi-code-extension';

const plugin: JupyterFrontEndPlugin<void> = {
  id: PLUGIN_ID,
  description:
    'Side panel listing Kimi Code sessions per project folder, with favorites, coloured terminal tabs, and one-click resume in a terminal.',
  autoStart: true,
  requires: [ILabShell],
  optional: [
    ILayoutRestorer,
    ISettingRegistry,
    ITerminalTracker,
    IDefaultFileBrowser,
    IColourfulTabs
  ],
  activate: async (
    app: JupyterFrontEnd,
    labShell: ILabShell,
    restorer: ILayoutRestorer | null,
    settingRegistry: ISettingRegistry | null,
    terminalTracker: ITerminalTracker | null,
    fileBrowser: IDefaultFileBrowser | null,
    colourfulTabs: IColourfulTabs | null
  ) => {
    const serverSettings = app.serviceManager.serverSettings;

    let status: IStatusResponse;
    try {
      status = await requestAPI<IStatusResponse>('status', serverSettings);
    } catch (err) {
      console.error(
        '[jupyterlab_kimi_code_extension] status check failed; panel will not be registered.',
        err
      );
      return;
    }

    if (!status.enabled) {
      console.info(
        '[jupyterlab_kimi_code_extension] `kimi` binary not found on PATH; panel disabled.'
      );
      return;
    }

    const widget = new KimiCodeSessionsWidget(
      app,
      status.root_dir || '',
      terminalTracker,
      fileBrowser,
      colourfulTabs
    );

    // Read the sidebar setting before docking so we add the widget to the
    // user's preferred side on first paint. Default to right.
    let currentSidebar: 'left' | 'right' = 'right';

    if (settingRegistry) {
      try {
        const settings = await settingRegistry.load(PLUGIN_ID);
        const initialSidebar = settings.get('sidebar').composite as string;
        if (initialSidebar === 'left' || initialSidebar === 'right') {
          currentSidebar = initialSidebar;
        }
        labShell.add(widget, currentSidebar, { rank: 600 });

        const apply = (): void => {
          const mode = settings.get('presentationMode').composite as string;
          // Any value other than 'path' is treated as 'name', so an
          // unrecognised saved value still maps to the name mode.
          widget.setPresentationMode(mode === 'path' ? 'path' : 'name');
          const limit = settings.get('recentLimit').composite as number;
          if (typeof limit === 'number') {
            widget.setRecentLimit(limit);
          }
          const yolo = settings.get('yoloMode').composite as boolean;
          widget.setYoloMode(!!yolo);
          // Defaults ON, so only an explicit `false` turns it off - a missing
          // value must not read as disabled.
          const coloured = settings.get('colouredTabs').composite as boolean;
          widget.setColouredTabs(coloured !== false);
          const sidebar = settings.get('sidebar').composite as string;
          if (
            (sidebar === 'left' || sidebar === 'right') &&
            sidebar !== currentSidebar
          ) {
            currentSidebar = sidebar;
            // Lumino re-parents the widget cleanly when add() is called
            // against a different area.
            labShell.add(widget, sidebar, { rank: 600 });
          }
        };
        apply();
        settings.changed.connect(apply);
      } catch (err) {
        console.warn(
          '[jupyterlab_kimi_code_extension] failed to load settings; using defaults',
          err
        );
        labShell.add(widget, currentSidebar, { rank: 600 });
      }
    } else {
      labShell.add(widget, currentSidebar, { rank: 600 });
    }

    // Register with the layout restorer so JL remembers whether the panel
    // was active/visible across browser reloads and restarts.
    if (restorer) {
      restorer.add(widget, WIDGET_ID);
    }

    app.commands.addCommand('kimi-code-sessions:refresh', {
      label: 'Refresh Kimi Code Sessions',
      execute: () => widget.refresh()
    });

    console.log(
      'JupyterLab extension jupyterlab_kimi_code_extension is activated!'
    );
  }
};

export default plugin;
