import './styles.css';
import {
  workspaceUrl,
  workspaceViewFromSearch,
  type WorkspaceView,
} from './app/router';
import {
  renderWorkspaceShell,
  type WorkspaceShellContent,
} from './app/shell';
import { BlueprintController } from './blueprint/controller';
import { LegacyControlCenterWorkspace } from './control-center/workspace';
import { HarvestExplorer } from './harvest/explorer';
import { KnowledgeWorkspace } from './kb/workspace';


const app = document.querySelector<HTMLDivElement>('#app');
if (!app) {
  throw new Error('Missing #app root.');
}
const root = app;
let workspaceView = workspaceViewFromSearch(window.location.search);

const legacyWorkspace = new LegacyControlCenterWorkspace(() => render());
const blueprintController = new BlueprintController(
  () => render(),
  (assetName) => legacyWorkspace.selectAssetByName(assetName),
  undefined,
  () => legacyWorkspace.ensureLoaded(),
);
const harvestExplorer = new HarvestExplorer(() => render());
const knowledgeWorkspace = new KnowledgeWorkspace(() => render());


function bindWorkspaceNavigation(): void {
  document.querySelectorAll<HTMLButtonElement>('[data-workspace]').forEach((button) => {
    button.addEventListener('click', () => {
      const requested = button.dataset.workspace;
      const nextView: WorkspaceView = requested === 'harvest' || requested === 'knowledge'
        ? requested
        : 'blueprint';
      if (nextView === workspaceView) {
        return;
      }
      legacyWorkspace.syncInputs();
      workspaceView = nextView;
      const url = workspaceUrl(window.location.href, workspaceView);
      window.history.replaceState({}, '', url);
      render();
      if (workspaceView === 'knowledge') {
        knowledgeWorkspace.ensureLoaded();
      }
    });
  });
}


function render(): void {
  let content: WorkspaceShellContent;
  if (workspaceView === 'harvest') {
    content = {
      body: harvestExplorer.render(),
      mainClass: 'workspace harvest-workspace',
    };
  } else if (workspaceView === 'knowledge') {
    content = {
      body: knowledgeWorkspace.render(),
      mainClass: 'workspace kb-main',
    };
  } else {
    content = {
      body: blueprintController.render({
        legacy: legacyWorkspace.renderLegacy(),
        experimental: legacyWorkspace.renderExperimental(),
      }),
      mainClass: 'workspace',
      topActions: legacyWorkspace.renderShellActions(),
    };
    root.innerHTML = renderWorkspaceShell(workspaceView, legacyWorkspace.version(), content);
    bindWorkspaceNavigation();
    legacyWorkspace.bind();
    blueprintController.bind();
    blueprintController.ensureLoaded();
    return;
  }

  root.innerHTML = renderWorkspaceShell(workspaceView, legacyWorkspace.version(), content);
  bindWorkspaceNavigation();
  if (workspaceView === 'harvest') {
    harvestExplorer.bind();
    harvestExplorer.ensureLoaded();
  } else {
    knowledgeWorkspace.bind();
    knowledgeWorkspace.ensureLoaded();
  }
}


render();
if (workspaceView !== 'blueprint') {
  legacyWorkspace.ensureVersionLoaded();
}
