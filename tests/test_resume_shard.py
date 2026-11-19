"""apply --resume and --shard: finishing a plan cut by a time limit, or splitting it over several jobs."""

import json

import netCDF4
import pytest
from test_parallel_apply import make_plan
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan, parse_shard, recorded_results, shard_files
from esgf_fix_tool.cli.main import app


def history(path):
    with netCDF4.Dataset(path) as ds:
        return getattr(ds["v"], "history", None)


def rows(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.mark.parametrize("text,expected", [("1/4", (1, 4)), (" 4 / 4 ", (4, 4)), ("1/1", (1, 1))])
def test_parse_shard(text, expected):
    assert parse_shard(text) == expected


@pytest.mark.parametrize("text", ["0/4", "5/4", "1", "a/b", "", "1/0"])
def test_parse_shard_refuses_bad_values(text):
    with pytest.raises(ValueError, match="k/K"):
        parse_shard(text)


def test_shards_cover_the_plan_once_whatever_was_already_done():
    files = {f"/sim/f{i:02d}.nc": [] for i in range(10)}
    shares = [shard_files(files, (k, 3)) for k in (1, 2, 3)]
    assert set().union(*shares) == set(files) and sum(map(len, shares)) == 10
    assert sorted(map(len, shares)) == [3, 3, 4]
    # computed on the whole plan: the same shares whatever is left to do
    assert shard_files(dict(list(files.items())[5:]) | files, (2, 3)) == shares[1]


def test_shards_fix_each_file_once_with_their_own_logs(tmp_path):
    plan = make_plan(tmp_path / "sim", count=5)
    totals = []
    for k in (1, 2):
        stats = apply_plan(plan, tmp_path / f"fixlog.shard-{k}.jsonl", shard=(k, 2), workers=2)
        totals.append(stats["files"])
        assert stats["shard"] == f"{k}/2" and not any(key.startswith("duplicates") for key in stats)
        assert all(r["shard"] == f"{k}/2" for r in rows(tmp_path / f"fixlog.shard-{k}.jsonl"))
    assert totals == [3, 2]
    seen = [r["file"] for k in (1, 2) for r in rows(tmp_path / f"fixlog.shard-{k}.jsonl")]
    assert sorted(seen) == sorted(plan.files)
    assert all(history(p) is None for p in plan.files)


def test_resume_skips_what_earlier_logs_record_as_done(tmp_path):
    plan = make_plan(tmp_path / "sim", count=4)
    first, second = sorted(plan.files)[:2]
    log = tmp_path / "fixlog.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in [
        {"time": "2026-11-18T10:00:00+00:00", "file": first, "status": "done"},
        {"time": "2026-11-18T10:00:00+00:00", "file": second, "status": "failed"},
        {"time": "2026-11-18T11:00:00+00:00", "file": sorted(plan.files)[2], "status": "done", "dry_run": True},
        {"kind": "file", "status": "done", "path": sorted(plan.files)[3]},          # an events line: ignored
    ]))
    stats = apply_plan(plan, tmp_path / "fixlog.resume.jsonl", resume=[log])
    assert stats["skipped_done"] == 1 and stats["files"] == 3          # the failed one is retried
    assert history(first) == "remove"                                  # skipped: untouched
    assert all(history(p) is None for p in sorted(plan.files)[1:])
    again = apply_plan(plan, tmp_path / "again.jsonl", resume=[log], skip_failed=True)
    assert again["skipped_done"] == 1 and again["skipped_failed"] == 1 and again["files"] == 2


def test_the_latest_record_of_a_file_wins(tmp_path):
    log = tmp_path / "a.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in [
        {"time": "2026-11-18T10:00:00+00:00", "file": "/f", "status": "failed"},
        {"time": "2026-11-18T12:00:00+00:00", "file": "/f", "status": "done"},
        {"time": "2026-11-18T12:00:00+00:00", "file": "/g", "status": "done"},
    ]))
    other = tmp_path / "b.jsonl"
    other.write_text(json.dumps({"time": "2026-11-18T13:00:00+00:00", "file": "/g", "status": "failed"}) + "\n")
    assert recorded_results([log, other]) == ({"/f"}, {"/g"})


@pytest.mark.parametrize("dry_run", [False, True])
def test_leftovers_of_interrupted_rewrites_are_removed_only_for_planned_files(tmp_path, dry_run):
    plan = make_plan(tmp_path / "sim", count=2)
    mine = tmp_path / "sim" / ".file0.nc.k2j3.fixtmp"
    other = tmp_path / "sim" / ".unplanned.nc.x.fixtmp"
    unrelated = tmp_path / "sim" / ".file0.nc.notes"
    for p in (mine, other, unrelated):
        p.write_text("debris")
    stats = apply_plan(plan, tmp_path / "log.jsonl", dry_run=dry_run)
    assert stats["leftovers_found" if dry_run else "leftovers_removed"] == 1
    assert mine.exists() == dry_run and other.exists() and unrelated.exists()


def test_cli_resume_and_shard(tmp_path):
    plan = make_plan(tmp_path / "sim", count=4)
    plan_path = tmp_path / "plan.json"
    plan.dump(plan_path)
    runner = CliRunner()
    base = ["apply", str(plan_path), "--only-under", str(tmp_path / "sim")]
    first = runner.invoke(app, [*base, "--shard", "1/2", "--fixlog", str(tmp_path / "fixlog.shard-1.jsonl")])
    assert first.exit_code == 0, first.output
    resumed = runner.invoke(app, [*base, "--resume", str(tmp_path / "fixlog.shard-1.jsonl"),
                                  "--fixlog", str(tmp_path / "fixlog.rest.jsonl")])
    assert resumed.exit_code == 0, resumed.output
    assert "'skipped_done': 2" in resumed.output and len(rows(tmp_path / "fixlog.rest.jsonl")) == 2
    bad = runner.invoke(app, [*base, "--shard", "3/2"])
    assert bad.exit_code == 2 and "k/K" in bad.output
    assert runner.invoke(app, [*base, "--skip-failed"]).exit_code == 2
