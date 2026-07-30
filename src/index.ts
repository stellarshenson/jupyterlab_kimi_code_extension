import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { requestAPI } from './request';

/**
 * Initialization data for the jupyterlab_kimi_code_extension extension.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyterlab_kimi_code_extension:plugin',
  description: 'Jupyterlab extension for Kimi Code offering session management, resume and navigation similar to the other extension - jupyterlab_kimi_code_extension',
  autoStart: true,
  activate: (app: JupyterFrontEnd) => {
    console.log('JupyterLab extension jupyterlab_kimi_code_extension is activated!');

    requestAPI<any>('hello', app.serviceManager.serverSettings)
      .then(data => {
        console.log(data);
      })
      .catch(reason => {
        console.error(
          `The jupyterlab_kimi_code_extension server extension appears to be missing.\n${reason}`
        );
      });
  }
};

export default plugin;
