#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::path::PathBuf;
    use std::process::Command;

    fn probe(operation: &str, case: &str) -> HashMap<String, String> {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repo = root.parent().expect("crate lives in repository");
        let python = std::env::var("PYTHON").unwrap_or_else(|_| "python".to_owned());
        let output = Command::new(python)
            .arg(root.join("probe.py"))
            .arg(operation)
            .arg(case)
            .current_dir(repo)
            .env("PYTHONPATH", repo)
            .env("QT_QPA_PLATFORM", "offscreen")
            .output()
            .expect("Python probe starts");
        assert!(
            output.status.success(),
            "{operation}/{case}: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        String::from_utf8(output.stdout)
            .expect("probe writes UTF-8")
            .lines()
            .map(|line| {
                let (key, value) = line.split_once('=').expect("key=value line");
                (key.to_owned(), value.to_owned())
            })
            .collect()
    }

    fn value<'a>(observed: &'a HashMap<String, String>, key: &str) -> &'a str {
        observed
            .get(key)
            .unwrap_or_else(|| panic!("missing {key}: {observed:?}"))
    }

    #[test]
    fn numeric_versions_and_invalid_versions() {
        assert_eq!(value(&probe("version", "1.10.0,1.9.0"), "comparison"), "1");
        assert_eq!(value(&probe("version", "1.9.0,1.10.0"), "comparison"), "-1");
        assert_eq!(value(&probe("version", "0.7.0,0.7.0"), "comparison"), "0");
        assert_eq!(value(&probe("version", "invalid"), "error"), "UpdateError");
    }

    #[test]
    fn manifest_decisions_and_etag() {
        for case in ["older", "same"] {
            let result = probe("check", case);
            assert_eq!(value(&result, "status"), "not_newer", "{case}");
            assert_eq!(value(&result, "package_url"), "", "{case}");
        }
        let newer = probe("check", "newer");
        assert_eq!(value(&newer, "status"), "update_available");
        assert_eq!(value(&newer, "remote"), "0.8.0");
        assert_eq!(
            value(&newer, "package_url"),
            "https://updates.example/app.tar.gz"
        );
        let inactive = probe("check", "inactive");
        assert_eq!(value(&inactive, "status"), "inactive");
        assert_eq!(value(&inactive, "requests"), "0");
        let unchanged = probe("check", "not_modified");
        assert_eq!(value(&unchanged, "status"), "not_modified");
        assert_eq!(value(&unchanged, "sent_etag"), "\"rev-1\"");
        assert_eq!(value(&unchanged, "etag"), "\"rev-1\"");
    }

    #[test]
    fn invalid_or_insecure_manifests_are_rejected() {
        for case in [
            "malformed",
            "not_object",
            "bad_version",
            "bad_schema",
            "bad_package_url",
            "oversize",
            "unsigned",
            "tampered",
            "wrong_key",
            "http",
        ] {
            let result = probe("check", case);
            assert_eq!(value(&result, "status"), "error", "{case}");
            assert_eq!(value(&result, "package_url"), "", "{case}");
        }
        let http = probe("check", "http");
        assert_eq!(value(&http, "requests"), "0");
        assert!(value(&http, "detail").contains("HTTPS"));
        let redirect = probe("redirect", "http");
        assert_eq!(value(&redirect, "error"), "UpdateError");
        assert!(value(&redirect, "detail").contains("HTTPS"));
    }

    #[test]
    fn download_verifies_stream_and_cleans_failures() {
        for case in ["valid", "short_write"] {
            let result = probe("download", case);
            assert_eq!(value(&result, "success"), "True", "{case}");
            assert_eq!(value(&result, "content_matches"), "True", "{case}");
            assert_eq!(value(&result, "files"), "1", "{case}");
        }
        for case in [
            "checksum",
            "cap",
            "truncated",
            "interrupted",
            "inside_install",
        ] {
            let result = probe("download", case);
            assert_eq!(value(&result, "success"), "False", "{case}");
            assert_eq!(value(&result, "files"), "0", "{case}");
        }
        assert_eq!(value(&probe("download", "inside_install"), "requests"), "0");
    }

    #[test]
    fn staging_preserves_live_install_and_external_data() {
        for case in ["links", "windows"] {
            let result = probe("stage", case);
            assert_eq!(value(&result, "success"), "True", "{case}");
            assert_eq!(value(&result, "staged"), "True", "{case}");
            assert_eq!(value(&result, "payload"), "True", "{case}");
            assert_eq!(value(&result, "live_marker"), "live", "{case}");
            assert_eq!(value(&result, "data_link"), "True", "{case}");
            assert_eq!(value(&result, "processed_link"), "True", "{case}");
            assert_eq!(value(&result, "external_data"), "local-only", "{case}");
        }
        assert_eq!(value(&probe("stage", "links"), "helper_executable"), "True");
        assert_eq!(value(&probe("stage", "windows"), "runtime_file"), "True");
    }

    #[test]
    fn unsafe_or_incompatible_archives_never_replace_live_install() {
        for case in [
            "internal_data",
            "data_root_link",
            "version_mismatch",
            "traversal",
            "absolute_link",
            "zip_symlink",
        ] {
            let result = probe("stage", case);
            assert_eq!(value(&result, "success"), "False", "{case}");
            assert_eq!(value(&result, "staged"), "False", "{case}");
            assert_eq!(value(&result, "live_marker"), "live", "{case}");
            assert_eq!(value(&result, "outside"), "False", "{case}");
        }
        assert_eq!(
            value(&probe("stage", "internal_data"), "preserved_data"),
            "research-data"
        );
        assert_eq!(
            value(&probe("stage", "data_root_link"), "preserved_data"),
            "research-data"
        );
        assert!(value(&probe("stage", "version_mismatch"), "error").contains("version"));
    }

    #[test]
    fn apply_helper_waits_swaps_and_rolls_back() {
        let waited = probe("apply", "wait_parent");
        assert_eq!(value(&waited, "ready"), "True");
        assert_eq!(value(&waited, "script_outside"), "True");
        assert_eq!(value(&waited, "backup_mentioned"), "True");
        assert_eq!(value(&waited, "before"), "live");
        assert_eq!(value(&waited, "exit"), "0");
        assert_ne!(value(&waited, "repeat_exit"), "0");
        assert_eq!(value(&waited, "live_marker"), "new");
        assert_eq!(value(&waited, "backup_marker"), "live");
        assert_eq!(value(&waited, "staging_exists"), "False");
        for case in ["failed_swap", "startup_rollback"] {
            let result = probe("apply", case);
            assert_ne!(value(&result, "exit"), "0", "{case}");
            assert_eq!(value(&result, "live_marker"), "live", "{case}");
        }
        assert_eq!(value(&probe("apply", "failed_swap"), "backup"), "False");
        assert_eq!(
            value(&probe("apply", "startup_rollback"), "old_relaunched"),
            "True"
        );
        let windows = probe("apply", "windows");
        assert_eq!(value(&windows, "ready"), "True");
        assert_eq!(value(&windows, "supported"), "False");
        assert_eq!(value(&windows, "spawned"), "False");
        assert_eq!(value(&windows, "restore_in_script"), "True");
        assert_eq!(value(&windows, "move_in_script"), "True");
        let spawned = probe("apply", "spawn_wait_parent");
        assert_eq!(value(&spawned, "spawned"), "True");
        assert_eq!(value(&spawned, "live_marker"), "live");
        assert_eq!(value(&spawned, "staged"), "True");
    }

    #[test]
    fn desktop_controller_checks_without_blocking_and_reuses_etag() {
        let disabled = probe("qt", "disabled");
        assert_eq!(value(&disabled, "enabled"), "False");
        assert_eq!(value(&disabled, "active"), "False");
        assert_eq!(value(&disabled, "calls"), "0");
        let etag = probe("qt", "etag");
        assert_eq!(value(&etag, "enabled"), "True");
        assert_eq!(value(&etag, "interval"), "60000");
        assert_eq!(value(&etag, "calls"), "2");
        assert_eq!(value(&etag, "first_etag"), "");
        assert_eq!(value(&etag, "second_etag"), "\"rev-1\"");
    }

    #[test]
    fn desktop_stages_once_and_retries_after_failed_download() {
        let staged = probe("qt", "stage_once");
        assert_eq!(value(&staged, "ready"), "1");
        assert_eq!(value(&staged, "attempts"), "1");
        assert_eq!(value(&staged, "staged"), "True");
        assert_eq!(value(&staged, "live"), "True");
        let retry = probe("qt", "retry");
        assert_eq!(value(&retry, "failures"), "2");
        assert_eq!(value(&retry, "attempts"), "2");
        assert_eq!(value(&retry, "first_etag"), "");
        assert_eq!(value(&retry, "second_etag"), "");
    }
}
