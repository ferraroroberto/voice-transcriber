# Take lifecycle

A take is one press of Record, from the tap to the transcript landing in the Text card. The browser recorder in `app/webapp/static/` runs it as a small state machine, declared in one module: `app/webapp/static/take.js`.

## Why it is declared

A take has modes, and the rules for moving between them used to live in five modules and a handful of flags. Two ways out of `recording` existed and only a tap on Stop cleaned up after itself, so a recorder or mic that ended on its own left the timer, level meter, mic notice and wake lock running. A throw while starting left the mode on `starting`, which the Record button ignores, so it did nothing until a reload.

Declaring the lifecycle gives every exit the same teardown, makes an unplanned move fail loudly, and gives the next change one place to extend a state instead of one more flag.

## The states

| State | Meaning |
|---|---|
| `idle` | No take in flight. Record starts one. |
| `starting` | Record was tapped; the mic grant and the server session are being set up. |
| `recording` | The mic is live and chunks stream to the PC. |
| `finishing` | Capture is over. The chunks flush, then the server runs ffmpeg and whisper. |

The set is closed. Adding a state means adding it to `TAKE_STATES` and to the transition table, and handling it in every reader.

## Legal moves

| From | To | When |
|---|---|---|
| `idle` | `starting` | Record or Continue is tapped |
| `starting` | `recording` | the recorder is armed and the mic is live |
| `starting` | `idle` | the mic is denied, the session create fails, or any setup step throws |
| `recording` | `finishing` | a tap on Stop, silence, the app being backgrounded, the recorder stopping or erroring, or the mic ending |
| `finishing` | `idle` | the take has settled, with a transcript or a failure message |

Every other move is refused: `transition(next, reason)` logs a console error and changes nothing, and returns `false`. A legal move leaves an info line (`[take] recording -> finishing (mic-lost)`), so a take that went wrong can be read from the console.

## One teardown

`endCapture()` in `app/webapp/static/recorder.js` owns what a take owes on its way out of `recording`: the timer, the level meter, the mic notice, the wake lock, the live-partials stream and the mic-ended listener. It is safe to call twice and runs from `beginFinish()`, which every exit goes through:

- a tap on Stop, and the silence auto-stop
- `finalizeForBackground()` (page hidden or discarded)
- the recorder's own `stop` event, `MediaRecorder.onerror`, and the `ended` event of the mic's track
- a throw after the mic went live, which finishes the take and keeps what was streamed

A throw before the mic went live drops the take instead: the recorder and the cached mic stream are released, a toast says why, and the move is `starting` to `idle`.

When the take ended without the user asking (recorder stopped, recorder error, mic disconnected), the reason shows in the status line and a toast.

## Per-take flags

`beginTake()` resets the facts that belong to one take in a single place: `forceAppend` (set only by Continue), `backgroundFinalized`, `vadStopFired`, `vadSilenceSince`, `hadDroppedChunk`, `partialVersion`, `pendingUploads`, `bytesSent` and the upload chain. `endTake()` clears the two that must not outlive a settled take.

## Who reads it

Readers ask `isTake('idle')`, `isTake('recording')` or `isTake('finishing')`; none compares `state.mode` to a string, and `state.mode` is written only by `take.js`.

| Module | Asks |
|---|---|
| `app/webapp/static/recorder.js` | whether Record starts or stops a take, and whether backgrounding finalises one |
| `app/webapp/static/app.js` | the page-visibility handler: finalise, release the cached mic, re-acquire the wake lock, show the "finalising" line |
| `app/webapp/static/level.js` | whether the silence auto-stop may fire |
| `app/webapp/static/partials.js` | whether a partial may write the live status line |
| `app/webapp/static/ui.js` | the Text card's context line and whether Polish is enabled |
| `app/webapp/static/history.js` | whether Redo and the deleted-take release may touch the session id |
| `app/webapp/static/polish.js` | whether Reset may clear the session |

A new reader calls `isTake()`. It never re-derives the mode from the recorder, the stream or a flag.

## Declared and tested

The machine is declared as `take` in `.fleet.toml` (`[[state_machines]]`), so the state rules of the conventions ratchet stop reporting `take.js` and keep reporting any other module that re-spells its states or writes its mode. It has no derive function because its state is stored by `transition()`, not inferred from other facts.

`tests/test_take_lifecycle.py` runs `take.js` under Node and checks every pair of states: the legal moves are taken, the rest are refused with one console error and no change. `app/webapp/static/__tests__/take.test.js` drives the real recorder against fakes for the exits that matter: a tap, silence, the recorder stopping by itself, a recorder error, the mic ending, a second tap while finishing, a throwing `new MediaRecorder`, a failed session create, a denied mic and a throw once the mic is live.

The server-side process status (whisper, the translate server), the tray hotkey controller and the partial worker are separate concerns and are not part of this machine.
