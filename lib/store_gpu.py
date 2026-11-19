"""
The store's GPU calls: the writes that record each `agent-desk gpu` call as it asks for its slots, gets them, and
releases them, with its run's totals, and the read of the waits `agent-desk gpu status` lists. `Store` in
`lib/store.py` inherits `GpuMixin`, whose methods use the store's `db`, `write`, `_event`, and `version`.

A call's record stays open until its wrapper releases the slots, which lib/gpu.sh does as it exits, even when killed
with a signal it can trap. A call killed outright leaves its record open, and no total or wait counts an open record,
so it never counts forever.
"""

from store_schema import GPU_SCHEMA, StoreError


class GpuMixin:
    """The methods of `Store` that write and read `gpu_calls`."""

    def gpu_asked(self, run, checkout, exclusive, asked, held_by=None, got=None):
        """
        Records a call that asks for its slots at `asked`, in epoch seconds, and returns its id: `run` is the run it
        belongs to or None, `held_by` names the checkouts that held the slots when it had to wait, and `got` is when
        it took them, for a call that took them before it recorded. Adds no event, since no total changes yet.
        """
        with self.write():
            return self.db.execute(
                "INSERT INTO gpu_calls (run, checkout, exclusive, held_by, asked, got) VALUES (?, ?, ?, ?, ?, ?)",
                (run, checkout, int(bool(exclusive)), held_by, asked, got)).lastrowid

    def gpu_got(self, call, got):
        """Records when a waiting call took its slots, in epoch seconds."""
        with self.write():
            self.db.execute("UPDATE gpu_calls SET got = ? WHERE id = ? AND got IS NULL AND released IS NULL",
                            (got, call))

    def gpu_released(self, call, released):
        """
        Records when a call released its slots, or stopped waiting for them, in epoch seconds, and adds the call to
        its run's totals with a `gpu-used` event, so the board shows them while the run still works. Returns False
        when the call doesn't exist or was released already.
        """
        with self.write():
            row = self.db.execute("SELECT run FROM gpu_calls WHERE id = ?", (call,)).fetchone()
            if row is None:
                raise StoreError(f"There is no GPU call {call}.")
            done = self.db.execute("UPDATE gpu_calls SET released = MAX(?, asked) WHERE id = ? AND released IS NULL",
                                   (released, call)).rowcount
            run = self.db.execute("SELECT id, project FROM runs WHERE id = ?", (row["run"],)).fetchone()
            if done and run is not None:
                queued, held = self.db.execute(
                    "SELECT SUM(COALESCE(got, released) - asked), SUM(COALESCE(released - got, 0)) FROM gpu_calls"
                    " WHERE run = ? AND released IS NOT NULL", (run["id"],)).fetchone()
                self.db.execute("UPDATE runs SET gpu_queued = ?, gpu_held = ? WHERE id = ?",
                                (queued / 60, held / 60, run["id"]))
                self._event(run["project"], None, "gpu-used", run=run["id"], call=call)
        return bool(done)

    def gpu_waits(self, since):
        """
        The released calls that asked for their slots at or after `since`, in epoch seconds, and had to wait, oldest
        first: each with its checkout, whether it was exclusive, who held the slots, and how many seconds it waited.
        None on a database older than `gpu_calls`.
        """
        if self.version < GPU_SCHEMA:
            return []
        rows = self.db.execute(
            "SELECT run, checkout, exclusive, held_by, asked, COALESCE(got, released) - asked AS waited"
            " FROM gpu_calls WHERE asked >= ? AND released IS NOT NULL AND held_by IS NOT NULL ORDER BY asked, id",
            (since,)).fetchall()
        return [{**dict(row), "exclusive": bool(row["exclusive"])} for row in rows]
