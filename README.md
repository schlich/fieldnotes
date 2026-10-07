# Fieldnotes

> [!NOTE]
> Fieldnotes is a personal project, built around one person's setup: a Nix
> flake configuration whose topics land through Tangled, GitHub Actions,
> Claude Code, and an IWE knowledge base. Feature requests are welcome, so
> feel free to open an issue or a pull request.

A terminal dashboard for what coding agents are doing and what they left
behind: provider usage windows, CI that needs attention, published topics
waiting to land, background tasks across machines, and recent decisions from
a knowledge base. It started as a wallpaper on a home server and grew into a
full Textual app that works the same over SSH.

## Panels

| #   | Panel            | Shows                                                                                                   | Source                                |
| --- | ---------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| 1   | Provider windows | Each usage window, colored by pace: points ahead of or behind an even burn through the window           | `ai-usagebar usage --json`            |
| 2   | CI               | Each workflow's verdict on main with its failure streak, and failing or running runs on live branches   | `gh api` (GitHub Actions)             |
| 3   | Waiting to land  | Published `jj-*` topics that have not reached main, oldest first; over two days old is highlighted      | Tangled knot XRPC                     |
| 4   | Background tasks | This host's agent background tasks and the reports other hosts push, with silent hosts flagged          | `$FIELDNOTES_TASKS_DIR/*.json`        |
| 5   | Librarian        | Recent decisions and notes                                                                              | `iwe find` in `$FIELDNOTES_IWE_ROOT`  |

Each source refreshes on its own schedule, and a slow or failing one never
blocks the others: its panel shows the error and keeps the last good data with
its age. The status line sums up what needs attention, such as
`CI 1 failing · 3 waiting, 1 over 2d · 2 running`.

## Modes and keys

The bottom of the screen always shows the current mode as a badge and, below
it, every key that works right now. The hints change with the mode, the
focused panel, and the highlighted row: `x Rerun failed` appears only on a
failed run, and `Y` names what it copies (a branch, a directory, a note key).

| Mode    | Enter with   | Leave with          | Keys                                                                                       |
| ------- | ------------ | ------------------- | ------------------------------------------------------------------------------------------ |
| NORMAL  | (start)      |                     | `j`/`k` move, `g`/`G` top/bottom, `tab`/`⇧tab`/`h`/`l` or `1`–`5` switch panel, `r`/`R` refresh panel/all, `q` quit |
|         |              |                     | `⏎` details, `o` open in a browser, `y` copy URL, `Y` copy value, `x` rerun failed CI jobs   |
| FILTER  | `/`          | `⏎` keeps, `esc` clears | type to narrow the panel's rows; `esc` in NORMAL also clears a kept filter            |
| DETAIL  | `⏎`          | `esc`, `⏎`, or `q`  | `j`/`k` scroll, `J`/`K` page, `o`, `y`, `Y`, `x`, `r` reload                                 |
| CONFIRM | `x`          | `y` runs, `n`/`esc` cancels | nothing leaves the machine until you press `y`                                     |

`o` opens a URL with `xdg-open` when there is a desktop session; elsewhere,
such as over SSH, it copies the URL instead. Copying uses the terminal's
clipboard (OSC 52), which also works over SSH.

The layout follows the terminal's width: the panels stack and scroll below
110 columns, sit in two columns up to 170, and in three beyond that.

## Install

Add the flake as an input:

```nix
inputs.fieldnotes = {
  url = "github:schlich/fieldnotes";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

The package puts `gh` on its PATH. `ai-usagebar` and `iwe` are not in
nixpkgs, so add them with `.override` when you have them:

```nix
inputs.fieldnotes.packages.${system}.default.override {
  runtimeTools = [ pkgs.gh ai-usagebar iwe ];
}
```

Or run it without installing: `nix run github:schlich/fieldnotes`, or
`uv run fieldnotes` from a checkout.

## Configuration

Each setting reads `FIELDNOTES_<NAME>` from the environment, and a flag of
the same name overrides it (`fieldnotes --ci-repo owner/repo`).

| Variable                      | Default                                           | Purpose                                  |
| ----------------------------- | ------------------------------------------------- | ---------------------------------------- |
| `FIELDNOTES_CI_REPO`          | `schlich/dotfiles`                                | GitHub repository for the CI panel       |
| `FIELDNOTES_TANGLED_KNOT`     | `https://knot1.tangled.sh`                        | Knot that hosts the topic branches       |
| `FIELDNOTES_TANGLED_REPO_DID` | the dotfiles repository's DID                     | Repository on that knot                  |
| `FIELDNOTES_TANGLED_REPO_URL` | `https://tangled.org/@schlich.tngl.sh/dotfiles`   | Web page for opening a topic's changes   |
| `FIELDNOTES_TASKS_DIR`        | `$XDG_STATE_HOME/fieldnotes/agent-tasks`          | Background task records                  |
| `FIELDNOTES_IWE_ROOT`         | `~/kb`                                            | IWE knowledge base                       |
| `FIELDNOTES_POLL_SECONDS`     | `30` (at least 5)                                 | Refresh interval for the remote sources  |

Provider credentials and `gh` authentication belong to the account running
the dashboard.

### Background task records

The tasks directory holds two kinds of JSON file. A hook on the machine where
agents run writes one record per task, `<id>.json`:

```json
{ "id": "b4x2", "description": "nix build", "cwd": "/home/me/src", "started": "2026-10-07T12:00:00-05:00", "finished": null }
```

A push job marks finished tasks and copies the host's list to other machines
as `<host>.json`, `{ "host": "laptop", "updated": "…", "tasks": [ … ] }`. A
host that has not pushed for 90 seconds shows as silent. On the recording
host, a task's detail view also shows the tail of its Claude Code output file.

## Development

```sh
nix develop       # Python with Textual and pytest, ruff, gh
pytest            # unit tests and headless runs of every mode
nix flake check   # builds the package (running the tests) and lints with ruff
nix fmt           # formats the Nix files
```

`uv run pytest` works too.
