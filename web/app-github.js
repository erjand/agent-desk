// The GitHub sidebar: each place the desk touches GitHub and how it last went, in the notice rows' tones, as one table
// in the Runners sidebar's look: the shown projects' main and agent branches, then the backup once.
import { html } from './vendor/preact-htm.js'
import { ProjectCell, projectWidth } from './app-runners.js'
import { githubRows, ready, shownDesks, ui } from './app-state.js'

/** The GitHub rows for the projects shown, as `githubRows` gives them, each as `{ desk, row }`. */
export function shownGithubRows() {
  return githubRows(shownDesks().filter(ready))
}

/** How many of the GitHub rows for the projects shown fail. */
export function githubFailing() {
  return shownGithubRows().filter(({ row }) => row.notice === 'warning').length
}

/** The rows in runs of one project each, in their order, so each project's rows share a group, as in Runners. */
export function groupsOf(rows) {
  const groups = []
  for (const one of rows) {
    const last = groups.at(-1)
    if (last && last[0].desk === one.desk) {
      last.push(one)
    } else {
      groups.push([one])
    }
  }
  return groups
}

/**
 * The GitHub sidebar, in History's column: its title with the failing count in the warning tone, then a table of what
 * the desk touches and how it last went, a group per project. On All a Project column names each group on its first
 * row and a rule separates one group from the next; the backup, which covers every project, names none.
 */
export function GitHub() {
  const rows = shownGithubRows()
  const all = ui.tab === 'all'
  const failing = rows.filter(({ row }) => row.notice === 'warning').length
  return html`<aside class="runners-side github-side" aria-label="GitHub"
    style=${{ '--chip-width': `${projectWidth()}ch` }}>
    <div class="side-head"><h2>GitHub</h2>
      ${failing > 0 ? html`<span class="counter warning">${failing} failing</span>` : null}</div>
    ${rows.length === 0 ? html`<p class="muted">No board has been read yet.</p>` : html`<table
      class=${`runners-table github-table ${all ? 'all' : ''}`}>
      <colgroup>
        ${all ? html`<col class="desk" />` : null}
        <col class="g-name" /><col class="g-note" />
      </colgroup>
      <thead><tr>
        ${all ? html`<th class="desk">Project</th>` : null}
        <th class="g-name">Push</th>
        <th class="g-note">Status</th>
      </tr></thead>
      ${groupsOf(rows).map(group => html`<tbody key=${group[0].desk?.id ?? ''}>${group.map(({ desk, row }, at) => html`
        <tr class="github-row" key=${row.key}>
          <${ProjectCell} desk=${desk?.id} chip=${all ? at === 0 && desk !== null : null} />
          <td class="g-name">${row.title}</td>
          <td class=${`g-note ${row.notice ?? 'muted'}`} title=${row.note}>${row.note}</td>
        </tr>`)}</tbody>`)}
    </table>`}
  </aside>`
}
