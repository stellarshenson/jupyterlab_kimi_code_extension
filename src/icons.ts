import { LabIcon } from '@jupyterlab/ui-components';

// Crescent moon (Kimi is Moonshot AI's CLI) - original two-arc glyph: an
// outer semicircle on the left, bitten from the right by a wider inner arc.
const kimiSvgStr = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">
  <g class="jp-icon3" fill="#616161">
    <path d="M8 1.5A6.5 6.5 0 1 0 8 14.5 8 8 0 0 1 8 1.5Z"/>
  </g>
</svg>`;

export const kimiIcon = new LabIcon({
  name: 'jupyterlab_kimi_code_extension:kimi',
  svgstr: kimiSvgStr
});

const starFilledSvgStr = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16">
  <path class="jp-icon3" fill="#616161" d="M12 2.5l2.9 6.4 7 .7-5.3 4.7 1.6 6.9L12 17.7l-6.2 3.5 1.6-6.9-5.3-4.7 7-.7z"/>
</svg>`;

export const starFilledIcon = new LabIcon({
  name: 'jupyterlab_kimi_code_extension:star-filled',
  svgstr: starFilledSvgStr
});

// Material-style icons matched verbatim to jupyterlab_trash_mgmt_extension so
// header/menu icons render at identical size and theme.
const refreshSvgStr = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16">
  <path class="jp-icon3" fill="#616161" d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/>
</svg>`;

export const refreshIcon = new LabIcon({
  name: 'jupyterlab_kimi_code_extension:refresh',
  svgstr: refreshSvgStr
});

const removeSvgStr = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16">
  <path class="jp-icon3" fill="#616161" d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM8 9h8v10H8V9zm7.5-5l-1-1h-5l-1 1H5v2h14V4h-3.5z"/>
</svg>`;

export const removeIcon = new LabIcon({
  name: 'jupyterlab_kimi_code_extension:remove',
  svgstr: removeSvgStr
});

const shieldSvgStr = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="13" height="13">
  <path class="jp-icon3" fill="#616161" d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 10.99h7c-.53 4.12-3.28 7.79-7 8.94V12H5V6.3l7-3.11v8.8z"/>
</svg>`;

export const shieldIcon = new LabIcon({
  name: 'jupyterlab_kimi_code_extension:shield',
  svgstr: shieldSvgStr
});

// Material "add" plus, same 16px/jp-icon3 treatment as the other header
// icons.
const addSvgStr = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16">
  <path class="jp-icon3" fill="#616161" d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>
</svg>`;

export const addIcon = new LabIcon({
  name: 'jupyterlab_kimi_code_extension:add',
  svgstr: addSvgStr
});

// Git-branch glyph (Octicons git-branch-16, MIT) - marks rows that carry
// parallel conversations and the Branch Session menu entries.
const branchSvgStr = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">
  <path class="jp-icon3" fill="#616161" d="M9.5 3.25a2.25 2.25 0 1 1 3 2.122V6A2.5 2.5 0 0 1 10 8.5H6a1 1 0 0 0-1 1v1.128a2.251 2.251 0 1 1-1.5 0V5.372a2.25 2.25 0 1 1 1.5 0v1.836A2.493 2.493 0 0 1 6 7h4a1 1 0 0 0 1-1v-.628a2.25 2.25 0 0 1-1.5-2.122Zm-6 0a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Zm8.25-.75a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5ZM4.25 12a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Z"/>
</svg>`;

export const branchIcon = new LabIcon({
  name: 'jupyterlab_kimi_code_extension:branch',
  svgstr: branchSvgStr
});

// Arrow-switch glyph (Octicons arrow-switch-16, MIT) - marks the
// "Switch and Manage Sessions" submenu.
const switchSvgStr = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">
  <path class="jp-icon3" fill="#616161" d="M5.22 14.78a.75.75 0 0 0 1.06-1.06L4.56 12h8.69a.75.75 0 0 0 0-1.5H4.56l1.72-1.72a.75.75 0 0 0-1.06-1.06l-3 3a.75.75 0 0 0 0 1.06l3 3Zm5.56-6.5a.75.75 0 1 1-1.06-1.06l1.72-1.72H2.75a.75.75 0 0 1 0-1.5h8.69L9.72 2.28a.75.75 0 0 1 1.06-1.06l3 3a.75.75 0 0 1 0 1.06l-3 3Z"/>
</svg>`;

export const switchIcon = new LabIcon({
  name: 'jupyterlab_kimi_code_extension:switch',
  svgstr: switchSvgStr
});

// Funnel copied verbatim from @jupyterlab/ui-components'
// `search/filter.svg` - the same image the file browser's filter
// toggle uses. The `class="jp-icon3"` lets JupyterLab's theme drive
// the fill, so the `fill="#FFF"` on the source path becomes effectively
// inert under the standard light/dark themes.
const filterSvgStr = `<svg xmlns="http://www.w3.org/2000/svg" width="16" viewBox="0 0 24 24">
  <path fill="#FFF" d="M14 12v7.88c.04.3-.06.62-.29.83a.996.996 0 0 1-1.41 0l-2.01-2.01a.99.99 0 0 1-.29-.83V12h-.03L4.21 4.62a1 1 0 0 1 .17-1.4c.19-.14.4-.22.62-.22h14c.22 0 .43.08.62.22a1 1 0 0 1 .17 1.4L14.03 12z" class="jp-icon3"/>
</svg>`;

export const filterIcon = new LabIcon({
  name: 'jupyterlab_kimi_code_extension:filter',
  svgstr: filterSvgStr
});
