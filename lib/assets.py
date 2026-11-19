"""Keeps the pictures `agent-desk shots` saves for reviews, in the state folder and the store's `assets` table.

    python3 assets.py save [--repo <owner/repo>] <review> <picture>...   prints each picture's hash, in order
    python3 assets.py prune [--days 30] [--keep <hash>]...               prints how many pictures it deleted

`save` copies each picture to `assets/<owner>__<repo>/<hash>.<extension>` in agent-desk's state folder, named by the
SHA-256 of its content, and records its hash, project, review, and path in `assets`. The web server serves it at
`/api/asset/<hash>`. A picture saved again keeps its file, and the row moves to the newer review, whose comment is the
newer one to show it.

`prune` deletes the files and rows of every project's pictures whose review closed more than `--days` days ago, except
the ones `--keep` names, such as those `agent-desk shots` saved in the same call for a review closed long ago.
"""

import argparse
import hashlib
import shutil
import sys
import time
from pathlib import Path

import store

ASSETS = store.STATE / "assets"
DAYS = 30


def folder(project, root=None):
    """The folder that holds a project's pictures."""
    return (Path(root) if root else ASSETS) / store.project_key(project)


def save(desk, project, number, pictures, root=None):
    """Copies each picture into the project's folder, records it against review `number`, and returns their hashes."""
    desk.item(project, number, "review")
    place = folder(project, root)
    place.mkdir(parents=True, exist_ok=True)
    saved = []
    for picture in map(Path, pictures):
        digest = hashlib.sha256(picture.read_bytes()).hexdigest()
        target = place / f"{digest}{picture.suffix.lower()}"
        if not target.exists():
            # Through a temporary name in the same folder, so the server never reads half a picture.
            partial = target.with_name(f".{target.name}.partial")
            shutil.copyfile(picture, partial)
            partial.replace(target)
        saved.append((digest, str(target)))
    with desk.write():
        desk.db.executemany(
            "INSERT INTO assets (hash, project, number, path) VALUES (?, ?, ?, ?) ON CONFLICT (hash) DO UPDATE"
            " SET project = excluded.project, number = excluded.number, path = excluded.path",
            [(digest, project, number, path) for digest, path in saved])
    return [digest for digest, _ in saved]


def prune(desk, days=DAYS, now=None, keep=()):
    """
    Deletes the pictures of reviews closed more than `days` days ago, except those whose hashes `keep` holds, and
    returns how many it deleted.
    """
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((now or time.time()) - days * 86400))
    with desk.write():
        old = desk.db.execute(
            "SELECT assets.hash, assets.path FROM assets JOIN items ON items.project = assets.project"
            " AND items.number = assets.number WHERE items.kind = 'review' AND items.state = 'closed'"
            " AND items.closed_at < ?", (cutoff,)).fetchall()
        old = [row for row in old if row["hash"] not in keep]
        desk.db.executemany("DELETE FROM assets WHERE hash = ?", [(row["hash"],) for row in old])
    for row in old:
        Path(row["path"]).unlink(missing_ok=True)
    return len(old)


def main(argv):
    top = argparse.ArgumentParser(prog="assets.py", description="Pictures `agent-desk shots` keeps for reviews.")
    verbs = top.add_subparsers(dest="verb", required=True)
    saving = verbs.add_parser("save")
    saving.add_argument("--repo", "-R")
    saving.add_argument("number", type=int)
    saving.add_argument("pictures", nargs="+")
    pruning = verbs.add_parser("prune")
    pruning.add_argument("--days", type=int, default=DAYS)
    pruning.add_argument("--keep", action="append", default=[])
    args = top.parse_args(argv)
    try:
        desk = store.Store()
        try:
            if args.verb == "save":
                print("\n".join(save(desk, args.repo or store.repo_here(), args.number, args.pictures)))
            else:
                print(prune(desk, args.days, keep=args.keep))
        finally:
            desk.close()
    except store.StoreError as problem:
        print(f"agent-desk: {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
