export type WorkspaceView = 'blueprint' | 'harvest';


export function workspaceViewFromSearch(search: string): WorkspaceView {
  return new URLSearchParams(search).get('view') === 'harvest'
    ? 'harvest'
    : 'blueprint';
}


export function workspaceUrl(
  currentHref: string,
  nextView: WorkspaceView,
): URL {
  const url = new URL(currentHref);
  if (nextView === 'harvest') {
    url.searchParams.set('view', 'harvest');
    return url;
  }
  url.searchParams.delete('view');
  url.searchParams.delete('q');
  url.searchParams.delete('node');
  url.searchParams.delete('resource');
  return url;
}
