"""Tests for agent-desk/lib/assets.py and lib/shots.sh, which keep a review's screenshots in the state folder."""

import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

import assets  # noqa: E402
import store  # noqa: E402

REPO = "org/game"
DAY = 86400


class AssetsCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.desk = store.Store(self.root / "state" / "agent-desk" / "desk.sqlite")
        self.addCleanup(self.desk.close)
        self.assets = self.root / "state" / "agent-desk" / "assets"

    def picture(self, name, content):
        path = self.root / name
        path.write_bytes(content)
        return path

    def review(self, title="Paint the fence"):
        return self.desk.create(REPO, "review", title, branch=f"agent/{title.replace(' ', '-')}")

    def rows(self):
        return [tuple(row) for row in
                self.desk.db.execute("SELECT hash, project, number, path FROM assets ORDER BY path")]

    def close(self, number, days_ago):
        self.desk.set_state(REPO, number, "closed")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * DAY))
        self.desk.db.execute("UPDATE items SET closed_at = ? WHERE project = ? AND number = ?", (stamp, REPO, number))

    def test_save_names_each_picture_by_its_hash_and_records_it_against_the_review(self):
        number = self.review()
        near, wide = self.picture("near.jpg", b"near"), self.picture("wide.JPG", b"wide")
        hashes = assets.save(self.desk, REPO, number, [near, wide], self.assets)
        self.assertEqual(hashes, [hashlib.sha256(b"near").hexdigest(), hashlib.sha256(b"wide").hexdigest()])
        folder = self.assets / "org__game"
        self.assertEqual((folder / f"{hashes[0]}.jpg").read_bytes(), b"near")
        self.assertEqual(sorted(path.name for path in folder.iterdir()), sorted(f"{one}.jpg" for one in hashes))
        self.assertEqual(sorted(self.rows()), sorted((one, REPO, number, str(folder / f"{one}.jpg")) for one in hashes))

    def test_a_picture_saved_again_keeps_its_file_and_moves_to_the_newer_review(self):
        first, second = self.review(), self.review("Fix the gate")
        same = self.picture("shot.jpg", b"same")
        [digest] = assets.save(self.desk, REPO, first, [same], self.assets)
        assets.save(self.desk, REPO, second, [same], self.assets)
        self.assertEqual(self.rows(), [(digest, REPO, second, str(self.assets / "org__game" / f"{digest}.jpg"))])

    def test_save_refuses_a_number_that_is_no_review(self):
        issue = self.desk.create(REPO, "issue", "Paint the fence")
        with self.assertRaises(store.StoreError):
            assets.save(self.desk, REPO, issue, [self.picture("shot.jpg", b"x")], self.assets)
        self.assertEqual(self.rows(), [])
        self.assertFalse(self.assets.exists())

    def test_prune_deletes_only_the_pictures_of_reviews_closed_over_thirty_days_ago(self):
        old, recent, still_open = self.review("Old"), self.review("Recent"), self.review("Open")
        saved = {number: assets.save(self.desk, REPO, number, [self.picture(f"{number}.jpg", str(number).encode())],
                                     self.assets)[0] for number in (old, recent, still_open)}
        self.close(old, 31)
        self.close(recent, 29)
        self.assertEqual(assets.prune(self.desk), 1)
        folder = self.assets / "org__game"
        self.assertFalse((folder / f"{saved[old]}.jpg").exists())
        self.assertEqual(sorted(row[2] for row in self.rows()), [recent, still_open])
        self.assertTrue((folder / f"{saved[recent]}.jpg").exists())
        self.assertEqual(assets.prune(self.desk), 0)

    def test_prune_keeps_the_pictures_it_is_told_to_keep(self):
        old = self.review("Old")
        kept, dropped = (assets.save(self.desk, REPO, old, [self.picture(f"{name}.jpg", name.encode())],
                                     self.assets)[0] for name in ("kept", "dropped"))
        self.close(old, 31)
        self.assertEqual(assets.prune(self.desk, keep=[kept]), 1)
        self.assertEqual([row[0] for row in self.rows()], [kept])


class ShotsCase(unittest.TestCase):
    """Runs lib/shots.sh with a magick that copies its input, against a store in a temporary state folder."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        fakes = self.root / "bin"
        fakes.mkdir()
        # The real magick writes a JPEG; this one copies the picture to the last argument.
        magick = fakes / "magick"
        magick.write_text(f'#!/usr/bin/env bash\nprintf %s "$1" >"{self.root}/magick-read"\ncp -- "$1" "${{!#}}"\n')
        magick.chmod(0o755)
        for name in ("gh", "git"):
            refused = fakes / name
            refused.write_text(f'#!/usr/bin/env bash\necho "{name} $*" >>"{self.root}/refused"\nexit 1\n')
            refused.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items()
                    if key not in ("AGENT_DESK_RUN", "AGENT_DESK_AUTHOR", "AGENT_DESK_UNIT")}
        self.env.update({"PATH": f"{fakes}:{os.environ['PATH']}", "GH_REPO": REPO,
                         "XDG_STATE_HOME": str(self.root / "state"), "XDG_CONFIG_HOME": str(self.root / "config")})
        self.database = self.root / "state" / "agent-desk" / "desk.sqlite"
        desk = store.Store(self.database)
        self.review = desk.create(REPO, "review", "Paint the fence", branch="agent/issue-4")
        desk.close()
        self.near = self.root / "filter_near.png"
        self.near.write_bytes(b"near")

    def shots(self, *args):
        return subprocess.run(["bash", str(LIB / "shots.sh"), *args], cwd=self.root, env=self.env,
                              capture_output=True, text=True)

    def query(self, sql):
        with sqlite3.connect(self.database) as db:
            return db.execute(sql).fetchall()

    def test_shots_prints_markdown_that_shows_each_picture_from_the_page(self):
        done = self.shots(str(self.review), str(self.near))
        self.assertEqual(done.returncode, 0, done.stderr)
        digest = hashlib.sha256(b"near").hexdigest()
        self.assertEqual(done.stdout, f"![filter_near](/api/asset/{digest})\n")
        path = self.root / "state" / "agent-desk" / "assets" / "org__game" / f"{digest}.jpg"
        self.assertEqual(path.read_bytes(), b"near")
        self.assertEqual(self.query("SELECT hash, project, number, path FROM assets"),
                         [(digest, REPO, self.review, str(path))])
        self.assertEqual(self.query("SELECT COUNT(*) FROM comments"), [(0,)])
        self.assertFalse((self.root / "refused").exists())

    def test_shots_comment_adds_the_markdown_to_the_review(self):
        done = self.shots("--comment", str(self.review), str(self.near))
        self.assertEqual(done.returncode, 0, done.stderr)
        digest = hashlib.sha256(b"near").hexdigest()
        self.assertEqual(done.stdout, f"![filter_near](/api/asset/{digest})\nCommented on #{self.review}.\n")
        body = f"![filter_near](/api/asset/{digest})\n\n\n<sub>Posted by agent-desk</sub>\n"
        self.assertEqual(self.query("SELECT number, author, body FROM comments"), [(self.review, "you", body)])
        self.assertFalse((self.root / "refused").exists())

    def test_a_picture_named_like_an_option_or_markdown_reads_as_a_file_with_plain_alt_text(self):
        odd = self.root / "-help]x\n[y\\.png"
        odd.write_bytes(b"odd")
        done = self.shots(str(self.review), odd.name)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, f"![-helpxy](/api/asset/{hashlib.sha256(b'odd').hexdigest()})\n")
        self.assertEqual((self.root / "magick-read").read_text(), f"./{odd.name}")

    def test_shots_keeps_the_pictures_it_saves_for_a_review_closed_long_ago(self):
        desk = store.Store(self.database)
        desk.set_state(REPO, self.review, "closed")
        desk.db.execute("UPDATE items SET closed_at = '2020-01-01T00:00:00Z'")
        desk.close()
        done = self.shots(str(self.review), str(self.near))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("Deleted", done.stderr)
        self.assertEqual(self.query("SELECT hash FROM assets"), [(hashlib.sha256(b"near").hexdigest(),)])

    def test_shots_refuses_a_review_the_store_does_not_hold(self):
        done = self.shots("99", str(self.near))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("no review #99", done.stderr)
        self.assertEqual(self.query("SELECT COUNT(*) FROM assets"), [(0,)])


if __name__ == "__main__":
    unittest.main()
