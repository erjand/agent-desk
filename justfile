# Start the agent-desk web page at http://127.0.0.1:7480 in this terminal
run:
    bin/agent-desk web run

# Serve made-up cards across three demo projects at http://127.0.0.1:<port>, for screenshots, from a new temporary folder with its own store and projects file
demo port="7481":
    #!/usr/bin/env bash
    set -euo pipefail
    root=$(mktemp -d -t agent-desk-demo-XXXXXX)
    node web/demo.ts "$root" >/dev/null
    echo "The demo desk is in $root."
    export XDG_CONFIG_HOME="$root/config" XDG_STATE_HOME="$root/state" AGENT_DESK_WEB_PORT={{port}}
    exec node web/server.ts

# Run every test suite: the scripts' Python tests, the inbox pane's, the web page's, shellcheck on the scripts, and gitleaks
test: && shellcheck gitleaks
    python3 -B -m unittest discover -s tests
    claude plugin test inbox
    node --test web/*.test.ts

# Check the shell scripts with the command the GitHub-hosted test workflows run
shellcheck:
    @command -v shellcheck >/dev/null || { echo "shellcheck is not installed; on Arch, run sudo pacman -S shellcheck" >&2; exit 1; }
    shellcheck bin/agent-desk lib/*.sh

# Fail on a secret in the history or the tree, without printing it; skips with a note where gitleaks isn't installed
gitleaks:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v gitleaks >/dev/null; then
        echo "gitleaks is not installed, so the secret scan is skipped; on Arch, run sudo pacman -S gitleaks" >&2
        exit 0
    fi
    gitleaks git --no-banner --redact --verbose .
    gitleaks dir --no-banner --redact --verbose .

# Open the release review: fold changelog.d into CHANGELOG.md and bump the version, by the entries or [major|minor|patch|X.Y.Z]; --dry-run previews it, and `publish` tags and publishes VERSION
release *args:
    python3 -B scripts/release.py {{args}}
