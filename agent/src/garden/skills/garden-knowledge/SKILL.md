---
name: garden-knowledge
description: Search Garden's knowledge base, and prepare, upload files to, preview, submit, cancel or undo import batches of documents with the garden command. Use when asked to find something in company documents, to check a collection's access rules, or to prepare a batch of documents for approval.
---

# Garden knowledge

Knowledge is a set of documents in collections.
A collection's access rules say which roles, and which departments, may read it, and a collection with no rules is readable by nobody.
Documents enter through an import batch: it is prepared in the background, previewed, submitted, and then applied live only when a person approves it.

Use `garden` (see the garden-basics skill).
Each command below is the operation id as words, and the MCP tool name is the same id with underscores.

## Search

- `garden knowledge search run --query "<question>" --json` (`knowledge.search.run`).
  Add `--limit <n>`, or `--as-of <date-time>` to search the knowledge as it was live at that moment.
- Every result names its source document and section, so cite them.
- Search returns only what the caller may read.
  An empty result can mean the person lacks access, not that the documents do not exist.
- Every search is logged.
  Only a person may read the log (`knowledge.search_log.list`, which needs `knowledge.search_log.read`).

## Collections and batches

- Health: `garden knowledge status read --json` (`knowledge.status.read`).
- Collections and their rules: `garden knowledge collection list --json` (`knowledge.collection.list`), or `collection read --collection-id <id>` (`knowledge.collection.read`).
- Batches: `garden knowledge batch list --json` (`knowledge.batch.list`), with `--collection-id` or `--state` to narrow it.
  Read one, with its files and its preparation report: `garden knowledge batch read --batch-id <id> --json` (`knowledge.batch.read`).

## Prepare a batch

1. Open one for a collection: `garden knowledge batch create --collection-id <id> --mode partial --json` (`knowledge.batch.create`).
   Use `full_set` only when the batch should replace the whole collection, because it also closes the live documents the batch does not name.
   Only one batch may be open per collection.
2. Add each file while the batch is still open for uploads: `garden knowledge batch upload --batch-id <id> --path <file> --json` (`knowledge.batch.upload`).
   Its MCP tool is `knowledge_batch_upload`, with `batch_id`, `path`, and an optional `translation_group`.
   A file with no `--translation-group` is its own group.
   The batch takes Markdown, text or PDF.
3. Start preparing: `garden knowledge batch prepare --batch-id <id> --json` (`knowledge.batch.prepare`).
   It runs in the background, so read the batch until its state is `prepared`, or `failed`.
4. Check the result: `garden knowledge batch preview --batch-id <id> --query "<question>" --json` (`knowledge.batch.preview`).
   Preview searches the batch before it goes live.
5. Ask for approval: `garden knowledge batch submit --batch-id <id> --json` (`knowledge.batch.submit`).
   This fixes the staged result.
   Tell the person it is waiting for their approval in the portal.
6. Cancel: `garden knowledge batch cancel --batch-id <id> --json` (`knowledge.batch.cancel`) while it is open or waiting.
   A request waiting for approval is withdrawn too.
   To change the content after a submit, cancel and open a new batch.

Batch states are `uploaded`, `preparing`, `prepared`, `awaiting_approval`, `applied`, `rejected`, `cancelled`, `failed` and `undone`.

## Undo (critical, admin only)

- `garden knowledge batch undo --batch-id <id> --json` (`knowledge.batch.undo`) restores the live documents and access rules the batch replaced.
  It is refused when a later batch has changed the same collection.
  This is a critical action, so tell the person first, and wait for them to confirm in the browser.

## Person only

These are not tools, and an agent login cannot run them: `knowledge.batch.approve` and `knowledge.batch.reject` (a person decides the batch in the portal), `knowledge.collection.create`, and `knowledge.document.erase`.
Say so when the person asks for one, and do not look for another route.

## Permissions

- `employee`: `knowledge.search` only, so every person may search.
- `finance`: collection view, prepare, submit and cancel.
- `admin`: all of finance's, plus decide, undo, erase and manage collections.

## Pitfalls

- Pass a file with `--path`, never inside `--body`.
- Files are accepted only while the batch is `uploaded`, so once it is prepared, a new file needs a new batch.
- A file over the portal's size limit is refused, and the error names the limit.
- A batch file replaces the live document with the same name.
- Approval is the person's, and the approver must not be the one who started the batch.
- Access rules are enforced in the search itself, and agents cannot change them.
  If a search misses documents the person should see, tell the person which collection and rule to check.
