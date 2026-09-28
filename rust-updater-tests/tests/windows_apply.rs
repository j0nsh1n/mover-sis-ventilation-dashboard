#![cfg(windows)]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

fn root() -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let path = std::env::temp_dir().join(format!(
        "mover-windows-update-{}-{unique}",
        std::process::id()
    ));
    fs::create_dir(&path).unwrap();
    path
}

fn build_fixture(root: &Path) -> PathBuf {
    let source = root.join("windows_app.rs");
    let binary = root.join("fixture.exe");
    fs::write(&source, include_str!("../fixtures/windows_app.rs")).unwrap();
    let status = Command::new("rustc")
        .args(["--edition=2024", "-o"])
        .arg(&binary)
        .arg(&source)
        .status()
        .unwrap();
    assert!(status.success());
    binary
}

fn apply_case(fixture: &Path, root: &Path, new_marker: &str) {
    let live = root.join("MOVER-SIS-Monitor");
    let staged = root.join("MOVER-SIS-Monitor.staging");
    fs::create_dir(&live).unwrap();
    fs::create_dir(&staged).unwrap();
    fs::copy(fixture, live.join("MOVER-SIS-Monitor.exe")).unwrap();
    fs::copy(fixture, staged.join("MOVER-SIS-Monitor.exe")).unwrap();
    fs::write(live.join("marker"), "old").unwrap();
    fs::write(staged.join("marker"), new_marker).unwrap();

    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let repo = crate_root.parent().unwrap();
    let python = std::env::var("PYTHON").unwrap_or_else(|_| "python".to_owned());
    let plan = Command::new(python)
        .arg(crate_root.join("probe.py"))
        .arg("windows_plan")
        .arg(&live)
        .arg(&staged)
        .current_dir(repo)
        .env("PYTHONPATH", repo)
        .output()
        .unwrap();
    assert!(
        plan.status.success(),
        "{}",
        String::from_utf8_lossy(&plan.stderr)
    );
    let script = String::from_utf8(plan.stdout).unwrap();
    let script = script.trim();
    assert!(Path::new(script).is_file());

    let stamp = root.join("launched");
    let output = Command::new("powershell.exe")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script])
        .env("MOVER_TEST_STAMP", &stamp)
        .output()
        .unwrap();
    let should_fail = new_marker == "new-fail";
    assert_eq!(
        output.status.success(),
        !should_fail,
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        fs::read_to_string(live.join("marker")).unwrap(),
        if should_fail { "old" } else { new_marker }
    );
    assert!(!staged.exists());
    let backup = root.join("MOVER-SIS-Monitor.backup");
    assert_eq!(backup.exists(), !should_fail);
    if backup.exists() {
        assert_eq!(fs::read_to_string(backup.join("marker")).unwrap(), "old");
    }
    let expected_launch = if should_fail { "old" } else { new_marker };
    for _ in 0..30 {
        if fs::read_to_string(&stamp).is_ok_and(|value| value == expected_launch) {
            break;
        }
        thread::sleep(Duration::from_millis(100));
    }
    assert_eq!(fs::read_to_string(&stamp).unwrap(), expected_launch);
    thread::sleep(Duration::from_secs(6));
    fs::remove_file(script).unwrap();
}

#[test]
fn windows_apply_and_rollback() {
    let root = root();
    let fixture = build_fixture(&root);
    let success = root.join("success");
    let failure = root.join("failure");
    fs::create_dir(&success).unwrap();
    fs::create_dir(&failure).unwrap();
    apply_case(&fixture, &success, "new");
    apply_case(&fixture, &failure, "new-fail");
    fs::remove_dir_all(root).unwrap();
}
