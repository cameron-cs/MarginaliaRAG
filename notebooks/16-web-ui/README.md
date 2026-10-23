# 16 · The web UI

Notebook: [16-web-ui.ipynb](16-web-ui.ipynb) · Code: `ui/` (React 19, Vite, TypeScript, React Router)

## What it does

The reader's and the admin's view of MarginaliaRAG, in the browser.

**Readers:**

- sign up and sign in;
- a shelf of books with covers and live status;
- uploads, with every problem shown next to its field;
- a book page to ask questions, watching each step of the agent live;
- answers with citation chips and whether they were checked against the book;
- refusals with the reason;
- answers reused from earlier questions, with "Ask again";
- answer settings (temperature, top P, top K, searches, seed);
- editing their prompts.

**Admins:**

- **Pipeline:** schedule, what is waiting, run all or chosen books, every batch with its tasks, logs,
  stop and retry.
- **Books:** covers, readers, questions.
- **Questions:** every reader's question with its outcome, tokens and details.
- **Users:** admin and active switches.
- **Links:** to Grafana and Evidently.

## Why?

Readers should not need an API client to ask a book a question, and the system's best feature,
answers you can check, needs to be visible: the steps, the citations, the verdict. Admins need
to run batches, read logs and see what readers ask without logging into four tools.

## For what

- Everyday use: adding books and asking questions.
- Operating the service: batches, failures, users, the questions log.
- Showing the system's behaviour live: steps streamed from the worker over the websocket.

## Simple explanation

A reading room with a librarian you can watch work: you hand over a question, and the screen shows
them thinking, searching the book, reading passages and checking their answer, until the answer
appears with its references underlined.

## Examples

- Upload an EPUB with an "Ignore all previous instructions" persona. Both fields turn red at once:
  "The file is not a PDF", "The prompt tries to change or reveal the assistant's rules."
- Ask "Who gave Harry the invisibility cloak?". The steps run "Thinking", "Searching the book",
  "Reading passages", "Checking the answer", then "Dumbledore gave Harry the invisibility cloak
  [Chapter Seventeen, passage 22] · Checked against the book".
- Ask "Who is Raskolnikov?" of Harry Potter: "Harry Potter … never mentions “Raskolnikov”, so it
  cannot answer that."

![The shelf](../../docs/screenshots/reader-shelf.png)
*The shelf.*

![The agent's steps, live](../../docs/screenshots/reader-live-answer.png)
*The agent's steps, live.*

![Answers with citation chips, a cached answer and a refusal](../../docs/screenshots/reader-book.png)
*Answers with citation chips, a cached answer and a refusal.*

![Answer settings](../../docs/screenshots/reader-answer-settings.png)
*Answer settings.*

![Upload problems](../../docs/screenshots/reader-upload-errors.png)
*Upload problems.*

![Admin: Pipeline](../../docs/screenshots/admin-pipeline.png)
*Admin: Pipeline.*

![Admin: Questions](../../docs/screenshots/admin-questions.png)
*Admin: Questions.*

![Admin: Books](../../docs/screenshots/admin-books.png)
*Admin: Books.*

## Technical

- **Serving:** it is built into the app image and served by the API from the same origin, so there is
  no CORS and the refresh cookie stays HttpOnly. Unknown paths fall back to `index.html` for the
  router; `/api/*` stays the API.
- **Auth:** `api.ts` keeps the access token in memory only and renews it a minute before it expires.
  One refresh runs at a time, and a 401 is retried once after a refresh. A reload signs in again
  through the cookie.
- **Live events:** `events.ts` opens `/api/ws` and sends the token as its first message. It reconnects
  with exponential backoff, and on 4401 renews the token and reconnects at once. `progress.ts` turns
  events into the step list and the shelf's statuses.
- **Admin pages:** `AdminPipeline.tsx` (waiting work, runs, tasks, the log sheet), `AdminQuestions.tsx`
  (log, stats, detail) and `Admin.tsx` (books, users).
- **Design:** an Apple-style design system in `styles.css`, with light and dark themes, a 640 px
  breakpoint and reduced motion. Sheets render in a portal on `<body>`.
- **Tests:** 57 Vitest tests, among them a regression test for an effect that returned Chromium's
  `scrollIntoView` promise.

## Tech examples

```bash
docker run --rm -v "$PWD/ui:/app" -w /app node:24.21.0-alpine sh -c "npm ci && npm test && npm run build"
# Hot reload on http://127.0.0.1:5180, proxying /api to the API on 8010:
docker run --rm -it -p 5180:5180 -e MARGINALIA_API=http://host.docker.internal:8010 \
  -v "$PWD/ui:/app" -w /app node:24.21.0-alpine npm run dev
```

## How it is connected to other steps

- **Talks only to:** the API (step 12).
- **Shows:** the guard's and cache's outcomes (step 10), the agent's steps and citations (step 09), the
  batches and their logs (step 13), and links to the dashboards and monitoring (steps 14–15).
