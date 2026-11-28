# dex-remote

A web page on Google Cloud where people you authorise can send tasks to a dex
running on your own machines, answer the questions those tasks ask, and follow
them to their summary and files. **Nothing in the cloud runs code**: a public
bucket serves a sign-in page, Firebase holds who may use it and a read-only
mirror of each task, Pub/Sub is the queue, and a private bucket holds the
application, attachments and results. Tasks run on your machines exactly like
any other dex task.

```
 browser ──sign in (Firebase Auth, Google)──────────────► users/<email> in Firestore?
   │                                                         no → "Not authorised"
   │  yes: app/app.js from the private bucket (Storage rules)
   │
   ├─ publish {task | answer} + ID token ──► Pub/Sub dex-tasks ──► dex-tasks-<project> (one per project)
   │   attachments ──► files bucket                                      │ pulled every 15 s
   │                                                                     ▼
   │                                             local dex (Settings: "Take tasks from dex-remote")
   │                                               checks token + users/<email>, runs the task here
   │                                                                     │
   └─ polls every 15 s ◄── Firestore projects/<p>/tasks/<id> (+ events) ◄┘ one-liners, summary, question
       downloads       ◄── files bucket projects/<p>/tasks/<id>/files/… ◄┘ uploaded as they appear
```

| Piece | Where | Who may write | Who may read |
|---|---|---|---|
| Sign-in shell | `dex-remote-web` bucket | Terraform | anyone (holds no data) |
| Application | `dex-remote-files/app/app.js` | Terraform | authorised users |
| Who is authorised | Firestore `users/<email>` | **you, by hand** | that user |
| Served projects, tasks, steps | Firestore `projects/…` | local dex | users granted the project |
| Queue | Pub/Sub `dex-tasks` | authorised users (IAM, synced by dex) | local dex |
| Attachments | `dex-remote-files/projects/<p>/attachments/<uid>/<sha256>` | their sender | sender, local dex |
| Results | `dex-remote-files/projects/<p>/tasks/<id>/files/…` | local dex | users granted the project |

Local dex uses one service account, `dex-local`, with full read/write on
Firestore, the files bucket and Pub/Sub.

## Authorising people

In the [Firebase console → Firestore](https://console.firebase.google.com/project/dex-remote/firestore),
collection `users`, add a document whose **id is the person's Google email in
lower case**:

| field | value |
|---|---|
| `role` | `"user"` or `"admin"` (string) |
| `projects` | map of project slug → `true`, e.g. `{ ml: true }` (ignored for `admin`) |
| `name` | optional |

- `user` sees and sends to the projects in `projects`, if some dex serves them.
- `admin` sees every project some dex serves. It is not a dex admin: no
  designer, no settings, nothing beyond sending tasks and answering questions.
- No document, or any other role (e.g. `"disabled"`): the page shows only
  "Not authorised" and the application is never sent to them.

A newly authorised person can read at once, and can send within about two
minutes, once a dex has added them to the topic's publishers. Removing them
takes effect on their next read, and their publish permission goes at the
next sync.

Google shows new users an "unverified app" warning, because the page asks for
Pub/Sub access. That is expected for a private app; *Advanced → continue*.

## Serving a project from a machine

1. `remote/setup-local.sh` writes `~/.config/dex/remote.json` and the
   `dex-local` key `~/.config/dex/remote-sa.json`. Copy both files to every
   other machine that should serve (`scp ~/.config/dex/remote*.json studio:.config/dex/`).
2. Restart that machine's dex (the bridge reads the config at start).
3. Open the project in dex → Settings → **Take tasks from dex-remote for
   `<project>`**. It is off for every project until you turn it on.

Each project gets its own Pub/Sub subscription; two machines serving the same
project share it, and each request goes to one of them. A request sent while
no machine serves its project waits in the queue for up to 7 days.

Attachments upload to the bucket the moment they are picked (or dropped on
the composer), each named by the SHA-256 of its bytes: its attachment id. A
request carries only the ids. Local dex uses a copy it already has, either one
it downloaded before (`datasets/<p>/remote/attachments/<id>/`) or any file in
the project's data with the same contents, and downloads only what it lacks,
checking the bytes against the id. A file already in the bucket is not
uploaded again.

What the page sees of a task: its state, one line per step ("Reading
utils.py", "Running tests", the agent's own short description of a shell
command, never the command, its output, the agent's prose or its thinking),
the questions it asks and their answers, the closing summary, and the package's
files. Tool *approvals* stay with the operator in local dex.

## First deploy

```bash
remote/deploy.sh            # builds remote/web, applies remote/terraform
```

Once per project, by hand, because Google offers no API for it:

1. Link a billing account (`gcloud billing projects link dex-remote --billing-account=…`).
2. Add Firebase to the project and accept its terms:
   console.firebase.google.com → *Add Firebase to a Google Cloud project* → dex-remote.
   (deploy.sh then imports it instead of creating it.)
3. Firebase console → Authentication → Sign-in method → **Google** → Enable.
4. Add yourself to `users` (above).

Terraform state is in `gs://dex-remote-tfstate`, so either machine can apply.
Terraform authenticates as whoever `gcloud` is signed in as.

## Costs

Estimated monthly Google Cloud bill (us-central1, list prices, late 2026),
assuming per task: ~100 Firestore writes (steps, state, files), ~500 reads from
someone watching it, ~20 MB of files of which half are downloaded, ~30 uploads.
The Claude usage of the tasks themselves is separate and much larger.

| | 1k tasks/mo | 10k tasks/mo | 100k tasks/mo |
|---|---|---|---|
| Firestore (free: 50k reads, 20k writes per day) | $0 | ~$1.50 | ~$25 |
| Storage: new files that month (20 GB / 200 GB / 2 TB) | ~$0.30 | ~$4 | ~$40 |
| Storage operations | ~$0.15 | ~$1.50 | ~$15 |
| Download egress (100 GB/mo free) | $0 | ~$0 | ~$110 |
| Pub/Sub (10 GiB/mo free) | $0 | $0 | $0 |
| Auth (free under 50k monthly users) | $0 | $0 | $0 |
| **Total** | **< $1** | **~$7** | **~$190** |

Storage accumulates: each month's files keep costing about $0.02/GB-month
until deleted. Set `files_retention_days` in Terraform to delete them from the
bucket after that many days (dex keeps its own copy). Egress depends only on
how much people download. An idle open tab costs ~3 reads per 15 s, and each
serving machine ~1,500 writes a day, both well inside the free tier.

## Files

- `terraform/`: everything in the project; `firestore.rules` and
  `storage.rules` are the access control.
- `web/`: `src/shell.ts` is the public sign-in page; `src/app/` is the
  application bundle (`vite.app.config.ts`).
- `../src/dex/remote.py`: the bridge in local dex; `../tests/test_remote.py`.
