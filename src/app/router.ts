export type WorkspaceView = 'blueprint' | 'harvest' | 'knowledge';


export function workspaceViewFromSearch(search: string): WorkspaceView {
  const view = new URLSearchParams(search).get('view');
  return view === 'harvest' || view === 'knowledge' ? view : 'blueprint';
}


export function workspaceUrl(
  currentHref: string,
  nextView: WorkspaceView,
): URL {
  const url = new URL(currentHref);
  if (nextView !== 'blueprint') {
    url.searchParams.set('view', nextView);
    return url;
  }
  url.searchParams.delete('view');
  url.searchParams.delete('q');
  url.searchParams.delete('node');
  url.searchParams.delete('resource');
  return url;
}
