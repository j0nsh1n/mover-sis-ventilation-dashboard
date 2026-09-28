use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use mover_update_manifest::{
    KEY_ENV, decode_seed, public_key_bytes, public_key_hex, verify_signed_manifest,
};

const RFC_SEED: &str = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60";

#[test]
fn cli_prints_public_key_and_signs_without_leaking_the_seed() {
    let dir = temp_dir();
    let linux = dir.0.join("MOVER-linux-x86_64.tar.gz");
    let windows = dir.0.join("MOVER-windows-x86_64.zip");
    fs::write(&linux, b"linux-bytes").unwrap();
    fs::write(&windows, b"windows-bytes").unwrap();
    let out = dir.0.join("manifest.json");

    let public_key = Command::new(env!("CARGO_BIN_EXE_mover-update-manifest"))
        .arg("public-key")
        .env(KEY_ENV, RFC_SEED)
        .output()
        .unwrap();
    assert!(public_key.status.success(), "{public_key:?}");
    let stdout = String::from_utf8(public_key.stdout).unwrap();
    let stderr = String::from_utf8(public_key.stderr).unwrap();
    assert_eq!(
        stdout,
        format!("{}\n", public_key_hex(&decode_seed(RFC_SEED).unwrap()))
    );
    assert!(!stdout.contains(RFC_SEED));
    assert!(!stderr.contains(RFC_SEED));

    let sign = Command::new(env!("CARGO_BIN_EXE_mover-update-manifest"))
        .args([
            "sign",
            "--version",
            "1.2.3",
            "--linux",
            linux.to_str().unwrap(),
            "--windows",
            windows.to_str().unwrap(),
            "--base-url",
            "https://updates.example.com/releases/v1.2.3",
            "--out",
            out.to_str().unwrap(),
        ])
        .env(KEY_ENV, RFC_SEED)
        .output()
        .unwrap();
    assert!(sign.status.success(), "{sign:?}");
    assert!(sign.stdout.is_empty());
    assert!(!String::from_utf8_lossy(&sign.stderr).contains(RFC_SEED));

    let bytes = fs::read(&out).unwrap();
    let seed = decode_seed(RFC_SEED).unwrap();
    let parsed = verify_signed_manifest(&bytes, &public_key_bytes(&seed)).unwrap();
    assert_eq!(parsed.version, "1.2.3");
    assert!(parsed.linux.url.ends_with("/MOVER-linux-x86_64.tar.gz"));
    assert!(parsed.windows.url.ends_with("/MOVER-windows-x86_64.zip"));
}

#[test]
fn cli_rejects_a_bad_key_without_echoing_it() {
    let marker = "NOT-A-REAL-SEED-DO-NOT-ECHO-zzzz";
    let output = Command::new(env!("CARGO_BIN_EXE_mover-update-manifest"))
        .arg("public-key")
        .env(KEY_ENV, marker)
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(stderr.contains("64 hex"));
    assert!(!stderr.contains(marker));
    assert!(output.stdout.is_empty());
}

#[test]
fn cli_does_not_create_output_when_a_package_is_empty() {
    let dir = temp_dir();
    let linux = dir.0.join("linux.tar.gz");
    let windows = dir.0.join("windows.zip");
    fs::write(&linux, b"").unwrap();
    fs::write(&windows, b"ok").unwrap();
    let out = dir.0.join("nested").join("manifest.json");
    let output = Command::new(env!("CARGO_BIN_EXE_mover-update-manifest"))
        .args([
            "sign",
            "--version",
            "0.1.0",
            "--base-url=https://updates.example.com/app",
            "--linux",
            linux.to_str().unwrap(),
            "--windows",
            windows.to_str().unwrap(),
            "--out",
            out.to_str().unwrap(),
        ])
        .env(KEY_ENV, RFC_SEED)
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(!out.exists());
    assert!(!dir.0.join("nested").exists());
}

struct TempDir(PathBuf);

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn temp_dir() -> TempDir {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let path =
        std::env::temp_dir().join(format!("mover-manifest-cli-{}.{nanos}", std::process::id()));
    fs::create_dir(&path).unwrap();
    TempDir(path)
}
