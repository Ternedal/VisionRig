from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_native_client_status_matches_landed_modules() -> None:
    android = ROOT / "clients" / "kaliv-android-producer"
    quest = ROOT / "clients" / "kaliv-quest-producer"
    core_readme = (ROOT / "clients" / "kaliv-producer-core" / "README.md").read_text(
        encoding="utf-8"
    )
    producer_docs = (ROOT / "docs" / "PRODUCER.md").read_text(encoding="utf-8")

    assert android.is_dir()
    assert quest.is_dir()

    assert "Android CameraX binding in `clients/kaliv-android-producer`" in core_readme
    assert (
        "Meta Quest 3/3S passthrough Camera2 binding in "
        "`clients/kaliv-quest-producer`"
    ) in core_readme

    assert "future Kaliv Android and Quest capture adapters" not in producer_docs
    assert (
        "platform-specific camera/passthrough adapters remain separate follow-up slices"
        not in producer_docs
    )
    assert "`clients/kaliv-android-producer`" in producer_docs
    assert "Meta Quest 3/3S Camera2 passthrough adapter" in producer_docs
