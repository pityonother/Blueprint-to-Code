export function renderBlueprintLegacyReports(content: string): string {
  return `
    <section class="blueprint-compatibility-view" aria-label="Legacy reports">
      <div class="panel blueprint-compatibility-note">
        <p class="eyebrow">Legacy</p>
        <h2>历史报告只读入口</h2>
        <p>这些 Markdown/按需报告不属于 Interpretation Contract v1；请用上方 revision 与 Evidence 追溯确认新结论。</p>
      </div>
      ${content}
    </section>
  `;
}

export function renderBlueprintExperimentalTools(content: string): string {
  return `
    <section class="blueprint-compatibility-view" aria-label="Experimental tools">
      <div class="panel blueprint-compatibility-note warning">
        <p class="eyebrow">Experimental</p>
        <h2>采集、重建与调试</h2>
        <p>这里的操作用于补充或重建证据；它们不会把 heuristic 提示提升为 confirmed statement。</p>
      </div>
      ${content}
    </section>
  `;
}
