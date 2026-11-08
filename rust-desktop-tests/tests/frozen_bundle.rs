use mover_desktop_contract_tests as support;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Read;
use std::path::Path;
use std::process::Command;

fn required_tags(text: &str) -> BTreeSet<String> {
    let mut tags = BTreeSet::new();
    let mut in_needs = false;
    for line in text.lines() {
        if line.starts_with("Version needs section") {
            in_needs = true;
        } else if line.starts_with("Version ") {
            in_needs = false;
        }
        if in_needs && let Some((_, name)) = line.split_once("Name: ") {
            let name = name.split_whitespace().next().expect("version name");
            if name.starts_with("GLIBC_") {
                tags.insert(name.to_owned());
            }
        }
    }
    tags
}

fn required_version(tag: &str) -> Result<(u32, u32), String> {
    if tag == "GLIBC_ABI_DT_RELR" {
        return Ok((2, 36));
    }
    let value = tag
        .strip_prefix("GLIBC_")
        .ok_or_else(|| format!("invalid GLIBC tag {tag}"))?;
    if !value
        .bytes()
        .all(|byte| byte.is_ascii_digit() || byte == b'.')
    {
        return Err(format!(
            "named ABI requirement {tag} is not supported by the GLIBC 2.39 baseline"
        ));
    }
    let parts: Result<Vec<u32>, _> = value.split('.').map(str::parse).collect();
    let parts = parts.map_err(|_| format!("malformed GLIBC requirement {tag}"))?;
    if !(2..=3).contains(&parts.len()) {
        return Err(format!("malformed GLIBC requirement {tag}"));
    }
    Ok((parts[0], parts[1]))
}

fn inspect(
    path: &Path,
    diagnostics: &Path,
    count: &mut usize,
    requirements: &mut BTreeMap<String, BTreeSet<String>>,
) {
    for entry in fs::read_dir(path).unwrap() {
        let entry = entry.unwrap();
        if entry.file_type().unwrap().is_dir() {
            inspect(&entry.path(), diagnostics, count, requirements);
            continue;
        }
        let mut magic = [0; 4];
        if fs::File::open(entry.path())
            .unwrap()
            .read(&mut magic)
            .unwrap()
            != 4
            || magic != *b"\x7fELF"
        {
            continue;
        }
        let output = support::execute(
            Command::new("readelf")
                .env("LC_ALL", "C")
                .arg("--version-info")
                .arg(entry.path()),
            diagnostics,
            &format!("elf-{count}"),
        );
        *count += 1;
        let text = String::from_utf8(output).unwrap();
        for tag in required_tags(&text) {
            requirements
                .entry(tag)
                .or_default()
                .insert(entry.path().display().to_string());
        }
    }
}

#[test]
#[ignore = "requires the actual frozen Linux bundle; run explicitly after building"]
fn frozen_bundle_runs_on_glibc_2_39_and_contains_icons() {
    let root = std::env::var_os("MOVER_FROZEN_BUNDLE").expect("MOVER_FROZEN_BUNDLE is required");
    let root = Path::new(&root);
    assert!(root.is_dir(), "frozen bundle directory is required");
    let diagnostics = support::scratch("frozen-bundle");
    let mut count = 0;
    let mut requirements = BTreeMap::new();
    inspect(root, &diagnostics, &mut count, &mut requirements);
    fs::write(
        diagnostics.join("requirements.txt"),
        format!("{requirements:#?}\n"),
    )
    .unwrap();
    assert!(count > 10, "no complete frozen ELF bundle inspected");
    let mut highest = (0, 0);
    let mut failures = Vec::new();
    for tag in requirements.keys() {
        match required_version(tag) {
            Ok(version) => {
                highest = highest.max(version);
                if version > (2, 39) {
                    failures.push(format!("requires {tag}"));
                }
            }
            Err(reason) => failures.push(reason),
        }
    }
    assert!(
        failures.is_empty(),
        "Inspected {count} ELF files; incompatible requirements:\n{}\nFull audit: {}",
        failures.join("\n"),
        diagnostics.display()
    );
    assert!(highest >= (2, 17), "no GLIBC version needs discovered");
    for icon in ["chevron-light.svg", "chevron-dark.svg"] {
        assert!(
            root.join("_internal/src/desktop/assets")
                .join(icon)
                .is_file(),
            "missing packaged {icon}"
        );
    }
    println!(
        "Inspected {count} ELF files; highest required GLIBC {}.{}",
        highest.0, highest.1
    );
}

#[test]
fn only_imported_glibc_versions_constrain_compatibility() {
    let tags = required_tags(
        "Version definition section '.gnu.version_d':\n Name: GLIBC_2.99\nVersion needs section '.gnu.version_r':\n Name: GLIBC_2.17 Flags: none\n Name: GLIBC_ABI_DT_RELR Flags: none\n Name: GLIBC_ABI_GNU2_TLS Flags: none\n Name: OTHER_9 Flags: none\n",
    );
    assert_eq!(
        tags,
        BTreeSet::from([
            "GLIBC_2.17".into(),
            "GLIBC_ABI_DT_RELR".into(),
            "GLIBC_ABI_GNU2_TLS".into()
        ])
    );
}

#[test]
fn named_requirements_are_checked_explicitly() {
    assert_eq!(required_version("GLIBC_ABI_DT_RELR").unwrap(), (2, 36));
    for tag in [
        "GLIBC_ABI_GNU2_TLS",
        "GLIBC_PRIVATE",
        "GLIBC_UNKNOWN_FEATURE",
    ] {
        let reason = required_version(tag).unwrap_err();
        assert!(reason.contains(tag) && reason.contains("not supported"));
    }
    assert_eq!(required_version("GLIBC_2.43").unwrap(), (2, 43));
    assert!(required_version("GLIBC_2").is_err());
    assert!(required_version("GLIBC_2..39").is_err());
}
