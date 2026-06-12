import os

from infra.repo_worker.workspace import build_repo_map, read_target_files, repo_dir_name


def test_repo_dir_name_uses_last_segment_and_sanitizes() -> None:
    assert repo_dir_name("nk-droid/test") == "test"
    assert repo_dir_name("owner/My.Repo_1") == "My.Repo_1"
    name = repo_dir_name("a/b c!d")
    assert "/" not in name and " " not in name and "!" not in name


def test_build_repo_map_lists_files_and_prunes_ignored_dirs(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").write_text("y", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("z", encoding="utf-8")

    lines = build_repo_map(tmp_path).splitlines()

    assert "README.md" in lines
    assert os.path.join("src", "a.py") in lines
    assert not any(".git" in line for line in lines)


def test_build_repo_map_caps_file_count(tmp_path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    assert len(build_repo_map(tmp_path, max_files=3).splitlines()) == 3


def test_read_target_files_reads_existing_strips_nodeids_and_guards_traversal(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("hello", encoding="utf-8")

    out = read_target_files(
        tmp_path,
        ["src/a.py::test_x", "missing.py", "../escape.py", "src/a.py"],
    )

    assert out == {"src/a.py": "hello"}  # node id stripped + deduped, others skipped


def test_read_target_files_skips_oversized(tmp_path) -> None:
    (tmp_path / "big.py").write_text("x" * 50, encoding="utf-8")
    assert read_target_files(tmp_path, ["big.py"], max_bytes=10) == {}
