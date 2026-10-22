# History, cleanup and logs

## History and cleanup

Every recording lands in `archive/YYYY/MM/DD/HH-MM-SS-<id>/` with
`raw.webm`, transcoded `audio.wav`, `transcript.txt`, `polished.txt`,
and `meta.json`. The directory is gitignored. Sessions older than 30
days are auto-deleted on app start (configurable in
`webapp_config.json`).

History lives on its **own tab** in the bottom navigation (fleet nav
contract) and refreshes itself every time you open it. It opens on the
**Latest take** card: the newest take, whatever app it came from, with a
full-width **Copy** (the full text from disk) and a ⋮ to its sheet.
Below it, the other takes sit in **day groups** (Today, Yesterday, then
weekday and date), one line each: the time, the start of the text, and a
meta line such as `61 words · 0:27 · This app`. The list loads the **10
newest** entries, and **Show older takes** at the bottom loads the next
10, continuing a day's group rather than repeating its heading.

On a wide desktop window (1100px or more, with a mouse) the nav becomes
a left rail, **Record** puts the recorder beside its Text card, and
**History** shows the picked take (the same full text, Copy, Redo, facts
and Delete as the phone's sheet) beside the list, opening on the newest
take. Settings keeps its centred column.

Each row names its source in plain text: *This app* for takes dictated
in this UI, or a consumer's own label (e.g. `app-launcher`) for
transcriptions triggered by another app through the
[session API](consuming-the-session-api.md). The server's generic
fallback `api` names the transport rather than the app, so a row hides
it, and the take's sheet shows it verbatim. A take in a language other
than the default carries a small language chip (e.g. `ES`). History is
the single attributable source of truth for transcription across the
fleet — externally triggered takes are captured *and* identifiable,
not just recoverable on disk.

A `Today: N takes · X wpm · ~Y min saved` line closes the Latest take
card — today's take count, an estimated dictation speed, and how
long that would have taken to type by hand (assuming a ~40 wpm typing
baseline; both figures are estimates, not precise measurements). It
reads `📊 No takes yet today` until the first completed take of the
day.

**Select** (on the first day's heading) turns the list into a picker:
tapping a row ticks it instead of copying it, and a bar at the bottom
offers **Copy**, which joins the picked takes' full text oldest first
with a blank line between them, and **Cancel**. **Settings › History ›
Delete all takes…** deletes every saved recording after a confirmation
sheet.

Each row follows the fleet's action-row pattern — the time on the left,
the take in the middle, one overflow button on the right:

- **Tap the row** — copies the full text from disk (not the 200-char
  preview the list payload carries) and flashes *Copied* on the row.
- **⋮ sheet** — the take's full text with one **Copy**; *Redo
  transcription* re-runs whisper on the saved raw audio (useful when a
  phone died mid-record and you want to pull the transcript afterwards);
  the take's facts (*Length*, *Language*, *Source*, *Served by*); and
  *Delete take* last, as red text: the first tap turns it into *Tap again
  to delete*, the second calls `DELETE /api/sessions/{id}` to remove that
  one take. Cleaner than deleting every take.

Every tab's header carries one live line beside its title: Record shows
the dictation language, or the one exception that applies (in order:
*Transcription offline*, Incognito, the Translate route, *Polish
unavailable*); History shows *N takes · kept D days*; Settings shows *All
services up* or names the first failing service.

### Persistent activity log

Unlike `archive/` (pruned after 30 days), every session-lifecycle event
— created, transcribed, transcribe/polish failures, polished, deleted —
is also written to a separate, much longer-lived SQLite log, retained
for 365 days and pruned on boot. It lives outside the git
checkout, at `C:\sqlite\voice-transcriber\activity.sqlite3` by default
— resolved by `src/runtime_data.py`, overridable (highest precedence
first) via `VT_ACTIVITY_DB_PATH` (full file path), `VOICE_TRANSCRIBER_DATA_DIR`
(this app's own data directory), or `FLEET_DATA_ROOT` (the fleet-wide
root; the app's directory is `<root>/voice-transcriber`). Because that
path is outside the checkout, it's invisible to `git ls-files` and
needs its own backup source. This is what backs
the analytics line above, and it's also where to look for *why*
something failed after its archive folder is long gone: `GET
/api/activity` (optional `event_type` / `since` epoch-seconds /
`limit` query params, newest first) returns the raw event trail. No
dedicated UI page for it yet — it's an API, curl or open it in a
browser.

### Usage log

The app records which views and controls get used, and on which device
class, so a design review reads real frequency instead of asking. The
vendored `usage-log` client posts batched events to this app's own origin
only; the vendored `src/usage_log.py` router stores them in
`usage.sqlite3` beside the activity log (same runtime-data directory,
override with `VT_USAGE_DB_PATH`). It sits behind the same bearer-token
gate as every other `/api` route. Controls opt in with a constant
`data-track="<id>"`; each tab and Settings is reported as a view. Never
recorded: transcripts, file names, audio details, query strings, IPs or
the raw user-agent (only event kind, view or control id, a per-visit
session id, device class and timestamps). `GET /api/usage/summary`
returns the counts.

