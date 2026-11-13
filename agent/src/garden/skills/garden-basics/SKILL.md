---
name: garden-basics
description: Use the garden command to act in the Garden portal for the person who signed in, including approving critical actions.
---

# Garden basics

Garden is the company portal.
The `garden` command gives you the tools that the signed-in person may use, and nothing more.

## Start

1. Run `garden whoami`.
   If it says you are not signed in, ask the person to run `garden login` and approve the login in the browser.
2. Run `garden tools --json` to see the tools you may use.
   Each tool shows its permission and whether it needs approval.

## Call a tool

Tool names are the operation ids with dots, such as `files.file.upload`, as a command: `garden files file upload`.

- Add `--json` to get JSON output and JSON errors.
- Pass path and query values as flags, such as `--sha256 <value>`.
- Pass a request body as `--body '<json>'`, `--body @file.json`, or `--body -` for stdin.

## Upload a file

Some tools take a file, such as `files.file.upload` and `knowledge.batch.upload`.
Give the file as a local path, and `garden` reads it and sends it to the portal.

- `garden files file upload --path <file> --json` uploads one file and returns its `sha256`, which other tools use to refer to it.
- In MCP the tool is `files_file_upload`, and its argument is `path`.
- Give an absolute path.
  A relative path is read from the folder the command runs in.
- A missing file, a folder, or a file that cannot be read is refused before anything is sent.
- The portal checks each file's bytes, and it refuses a type it does not accept and a file over its size limit.
  The error names the limit, so tell the person which one applies.

## Approval for critical actions

Some tools are marked `approval needed`.
When you call one, the portal does not run it yet.
The command prints a link, and the person must open that link in the browser and approve the action.
The command waits, then runs the action once the person approves it.

- Tell the person what the action is and why, before you call it.
- Do not try to get around the wait, and do not call the action again while it waits.
- If the person rejects it or the approval expires, the action did not run. Do not retry unless the person asks.

## Errors

Errors say what went wrong and what to do next in the `fix` field.
A 403 means the person does not have the permission.
A 429 means you have made too many requests for now, so wait a minute.

## Model access

The model gateway is an OpenAI-compatible endpoint on the portal, at `/api/agent/model/v1`.
It takes the same Agent login as a bearer token, and it limits requests per minute and tokens per day.
To see today's usage, run `garden agent my_model_usage read`.
