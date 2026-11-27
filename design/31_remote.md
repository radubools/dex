# 31 · dex-remote

People who are not on this machine send tasks from a page on Google Cloud;
the tasks run here like any other. The cloud side, how to authorise people,
and the costs are in [`remote/README.md`](../remote/README.md). This page is
the local half, `src/dex/remote.py`.

## The switch

`remote:<slug>` in `settings`, set from Settings → *Take tasks from dex-remote
for `<project>`*. Absent means off, so every project starts off. The bridge
starts only when `~/.config/dex/remote.json` (or `DEX_REMOTE_CONFIG`) exists;
tests point that at nothing (`tests/conftest.py`).

## One tick (every 15 s)

1. Every two minutes: write `projects/<slug>` for the projects switched on
   (this machine's `machines.<name>` only), and set the Pub/Sub topic's
   publishers to the people Firestore `users/<email>` authorises.
2. For each project switched on: make its filtered subscription if needed,
   pull, and handle each message:
   - `kind: task`: check the Firebase ID token in the message was valid at
     `publishTime` (Pub/Sub does not say who published), check
     `users/<email>` allows the project, fetch attachments from the sender's
     own upload folder, `TaskManager.submit` into a `Remote · <email>` thread,
     record `remote_tasks`, write the request as accepted (or rejected, with
     why), ack.
   - `kind: answer`: same checks, then `TaskManager.answer` on whichever
     attempt of that task holds the question.
3. For each `remote_tasks` row: read new events of the task's chain and write
   them as one-liners (`summarize`: state changes, one line per tool step,
   questions and answers, errors, the closing summary — no prose, thinking,
   inputs or outputs); upload changed package files and list them as
   available once uploaded. Nothing is written when nothing changed, so the
   page's incremental poll costs one read.

## Table

`remote_tasks(task_id, project, request_id UNIQUE, email, last_seq,
last_state, files, done)`: `request_id` keeps a redelivered message from
queueing twice; `last_seq` is how far the events are mirrored; `files` is what
was uploaded (`path → [size, mtime_ns]`).

## Improvement opportunities

- **Approvals stay local.** A task waiting on a tool approval shows "waiting
  for the operator" on the page and nothing more; only questions can be
  answered remotely.
- **No follow-ups or cancel from the page.** A remote user can start a task and
  answer it, not ask it for a change or stop it; both would be another message
  `kind` on the same queue.
- **The ID token check has a horizon.** A request is believed if its token was
  valid at `publishTime`, which needs the signing key; Google stops publishing
  old keys after some days, so a request that waited longer than that in the
  queue is rejected and has to be sent again.
- **Publishers lag grants by up to two minutes**, and an email with no Google
  account is skipped (`BridgeStatus.refused`) rather than shown anywhere but
  the Settings status.
- **Files are uploaded whole.** A large package re-uploads a file on every
  change; there is no resumable or chunked upload, and nothing past 200 MB.
- **The thread is per person per project**, created on first use; there is no
  way to pick another from the page.
