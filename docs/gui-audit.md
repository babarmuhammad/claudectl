# GUI Audit — claudectl

## Screens/routes
Routes from `claude_sessions/gui_api.py` (HTTP endpoints served by the GUI):

| Route | Handler | Description |
|-------|---------|-------------|
| `/api/job` | POST — spawns background Job | Run a job (plan, review, memory build, lessons scan) |
| `/api/job/<id>` | GET — job poll | Returns job status, messages, gate state |
| `/api/job/<id>/decide` | POST — approve/reject gate | Sets gate decision for approval-gated jobs |
| `/api/job/<id>/cancel` | POST — cancel job | Sets cancelled flag on a running job |
| `/api/launch` | POST — launch Claude Code | Launches Claude Code with chosen model/effort/options |
| `/api/sessions` | GET — list active sessions | Sessions for a project |
| `/api/transcript` | GET — session transcript | Full message list |
| `/api/session/meta` | GET — session metadata | Lines of meta info |
| `/api/session/archive` | POST — archive session | Moves session to archive |
| `/api/session/restore` | POST — restore session | Restores from archive |
| `/api/session/delete` | POST — delete session | Permanently deletes |
| `/api/session/export` | POST — export as markdown | Downloads session as file |
| `/api/session/tags` | GET/POST — read/write tags | Per-session tags |
| `/api/session/changed-files` | GET — list changed files | Files touched in session |
| `/api/rename` | POST — rename session | Rename session title |
| `/api/usage/daily` | GET — daily usage | Token counts for last N days |
| `/api/usage/projects` | GET — per-project costs | Cost breakdown by project |
| `/api/search-index` | GET — search index | All session haystacks for search |
| `/api/settings` | GET/POST — read/write settings | Full settings.json |
| `/api/settings/path` | GET — settings file path | Returns path to settings file |
| `/api/system-prompt` | GET/POST — read/write system prompt | Per-project system prompt |
| `/api/account/terminal` | POST — open terminal for account | Spawns PowerShell for an account |
| `/api/accounts` | GET — all accounts | List configured accounts |
| `/api/agents/list` | GET — list subagents | Agent definitions from library+project |
| `/api/agents/add` | POST — add agent | Add agent to project |
| `/api/agents/remove` | POST — remove agent | Remove agent from project |
| `/api/hooks/list` | GET — list hooks | Hook definitions |
| `/api/hooks/toggle` | POST — toggle hook | Enable/disable hook |
| `/api/hooks/add` | POST — add hook | Add hook template |
| `/api/hooks/save` | POST — save/update hook | Edit hook command |
| `/api/mcp/status` | GET — MCP server status | MCP server running states |
| `/api/connections` | GET — architecture graph | Module dependency data |
| `/api/graph-html` | GET — rendered graph | Full architecture graph HTML |
| `/api/memory/state` | GET — memory state | Entity/lesson counts, timestamps |
| `/api/memory/autoscan` | POST — trigger memory scan | Background memory refresh |
| `/api/memory/progress` | GET — memory build progress | Progress string for current build |
| `/api/memory/recall` | POST — query memory | Semantic recall search |
| `/api/lessons` | GET — list lessons | Lessons with confidence scores |
| `/api/lessons/action` | POST — approve/pin/evict lesson | Lesson management |
| `/api/workspace-status` | GET — workspace module status | Status per module |
| `/api/worklog` | GET — recent work entries | Worklog entries for project |
| `/api/worklog/toggle` | POST — toggle worklog on/off | Enable/disable worklog tracking |
| `/api/plan-execute` | POST — run plan | Generate + execute a plan |
| `/api/review` | GET/POST — review | Code review of uncommitted changes |
| `/api/global-search` | GET — global search | Full-text search across all sessions |
| `/api/account-folders` | GET — account project folders | List folders for account |
| `/api/preview-suggestions` | POST — path suggestions | Path completion for open-by-path |

## Theme mechanism
Themes live in `config.py` settings. On page load, JS function `applyTheme(name)` reads `ST.themes[name]` (a dict of CSS variable values) and sets them on `document.documentElement.style`. Theme names come via the main `/api/settings` response (which includes `ST.theme` and `ST.themes`). User can toggle themes via settings page. No localStorage persistence — theme resets on reload.

## Job model fields (from gui_api.py Job class)
- `id`: string
- `kind`: string (plan, review, memory_build, lessons_scan)
- `status`: string (running, awaiting, done, cancelled)
- `label`: string
- `messages`: list of {text, ok}
- `gate`: dict with {title, diff, decision, on_decision}
- `result`: any (stored on completion)
- `error`: string (optional)
- `cancelled`: bool (added in this redesign)
- `started_at`: float (time.time())
- `on_done`: callable

## Design issues
1. **Toast is a single DOM element** — `$('#toast')`. Multiple toasts overwrite; no stacking. Urgent and info toasts collide.
2. **No loading state component** — each view uses inline `<span class="spin"></span>` and checks null elements. No standard pattern.
3. **No cancel button during job execution** — Cancel exists for the gate modal but not during `running` state; user must force-kill the server.
4. **Theme not persisted** — `applyTheme()` runs from `ST.theme` (server state), reset on every page load. No localStorage fallback or preference memory.
5. **Account not persisted either** — previous account selection lost on reload. Same fix as theme.
6. **No error field on job status response** — job failure surfaces only via `toast('Failed','err')` with no error detail shown to user.
7. **No responsive layout beyond bento** — only the `.bento` grid has a `@media` breakpoint. Other pages (session list, memory, plan/execute) don't adapt at all.
8. **Missing `-webkit-font-smoothing` context** — The body has `-webkit-font-smoothing:antialiased` and `font-synthesis:none` which are correct but no fallback for non-WebKit renderers.
9. **No keyboard shortcut hints** — Users cannot Tab-navigate the session list actions; buttons are `opacity:0` until hover, making keyboard navigation invisible.
10. **No visible scrollbar on sidebar project list** — `.plist` has `overflow-y:auto` but no visible scroll indicator for long lists.
11. **Flash messages inline** — `toast()` uses a single fixed-element toast; no backbone for different message types (error vs success vs info).
12. **No in-place edit for plan steps** — generated plan appears read-only; user must accept or reject the whole thing.
