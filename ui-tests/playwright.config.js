/**
 * Configuration for Playwright using default from @jupyterlab/galata
 *
 * `JLAB_TEST_PORT` overrides the test server port (default 8888) so the
 * suite can run on machines where 8888 is already taken by a live
 * JupyterLab/JupyterHub. Galata's fixtures honour the matching baseURL.
 */
const baseConfig = require('@jupyterlab/galata/lib/playwright-config');

const port = process.env.JLAB_TEST_PORT ?? '8888';

module.exports = {
  ...baseConfig,
  use: {
    ...baseConfig.use,
    baseURL: `http://localhost:${port}`
  },
  webServer: {
    command: `jlpm start --port ${port} --ServerApp.port_retries=0`,
    url: `http://localhost:${port}/lab`,
    timeout: 120 * 1000,
    reuseExistingServer: !process.env.CI
  }
};
