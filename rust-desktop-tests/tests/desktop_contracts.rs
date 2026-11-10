use mover_desktop_contract_tests as support;
use support::{files, probe, scratch};

#[test]
fn emr_is_read_only_across_output_routes() {
    for route in ["pipeline", "scan", "settings"] {
        for variant in ["equal", "inside", "symlink", "external", "sibling"] {
            let dir = scratch(&format!("{route}-{variant}"));
            let mut prepared = probe("prepare", "source", &dir);
            assert!(prepared.number("source_files") >= 3);
            prepared.finish();
            let before = files(&dir.join("source"));
            let mut observed = probe(route, variant, &dir);
            let error = observed.text("error");
            let output = observed.text("output");
            if variant == "sibling" {
                assert!(error.is_empty(), "{route}: {error}");
                assert!(std::path::Path::new(&output).is_dir());
            } else {
                assert!(
                    !error.is_empty(),
                    "{route}/{variant} allowed writing inside EMR"
                );
                assert!(error.contains("EMR"), "unexpected failure {error}");
            }
            observed.finish();
            assert_eq!(
                before,
                files(&dir.join("source")),
                "raw EMR changed for {route}/{variant}"
            );
        }
    }
    let dir = scratch("custom-source-default");
    let mut observed = probe("default_output", "custom", &dir);
    let source = observed.text("source");
    let output = observed.text("output");
    assert!(!std::path::Path::new(&output).starts_with(source));
    observed.finish();
}

#[test]
fn matching_provenance_loads_and_other_source_is_rejected() {
    for variant in ["matching", "mismatch"] {
        let dir = scratch(variant);
        let mut observed = probe("provenance", variant, &dir);
        let error = observed.text("error");
        let ids = observed.text("case_ids");
        if variant == "matching" {
            assert!(error.is_empty());
            assert_eq!(ids, "SYN_A");
        } else {
            assert!(error.contains("different EMR"));
            assert!(ids.is_empty());
        }
        observed.finish();
    }
}

#[test]
fn switching_source_clears_frames_and_rejects_delayed_job() {
    let dir = scratch("switch-source");
    let mut observed = probe("switch", "delayed", &dir);
    assert_eq!(observed.number("frames_after_switch"), 0);
    assert_eq!(observed.number("facts_after_switch"), 0);
    assert_eq!(observed.text("case_ids"), "SYN_B");
    assert_eq!(observed.text("case_ids_after_late_failure"), "SYN_B");
    assert_eq!(observed.number("stale_dialogs"), 0);
    observed.finish();
}

#[test]
fn worker_errors_keep_private_details_out_of_ui() {
    for operation in [
        "load",
        "pipeline",
        "case",
        "research",
        "minute-question",
        "scan-load",
    ] {
        let dir = scratch(operation);
        let mut observed = probe("error", operation, &dir);
        let message = observed.text("message");
        let log = observed.text("log");
        assert!(message.contains(&format!("{operation}:")), "{message}");
        for secret in [
            "FICTIONAL_PRIVATE_MARKER",
            "private-record.csv",
            "Traceback",
        ] {
            assert!(!message.contains(secret), "UI leaked {secret}");
        }
        assert!(log.contains("FICTIONAL_PRIVATE_MARKER"));
        assert!(log.contains("private-record.csv"));
        assert_eq!(observed.number("log_mode"), 0o600);
        observed.finish();
    }
    let dir = scratch("existing-log");
    let mut observed = probe("error", "existing-log", &dir);
    assert!(
        !observed
            .text("message")
            .contains("FICTIONAL_PRIVATE_MARKER")
    );
    assert!(observed.text("log").starts_with("earlier entry\n"));
    assert_eq!(
        observed.number("log_mode"),
        0o600,
        "a shared log stays readable by others"
    );
    observed.finish();
    let dir = scratch("log-failure");
    let mut observed = probe("error", "log-failure", &dir);
    let message = observed.text("message");
    assert!(message.contains("diagnostic log could not be written"));
    assert!(!message.contains("FICTIONAL_PRIVATE_MARKER"));
    observed.finish();
}

#[test]
fn announcements_are_requested_once_per_message() {
    let dir = scratch("announcements");
    let mut observed = probe("announcements", "status-answer", &dir);
    assert_eq!(observed.number("repeated_status_count"), 1);
    assert_eq!(observed.number("status_bar_count"), 1);
    assert_eq!(observed.number("answer_count"), 1);
    let event_types = observed.text("event_types");
    assert!(!event_types.is_empty());
    assert!(event_types.lines().all(|kind| kind == "Announcement"));
    observed.finish();
}

#[test]
fn fields_and_actions_expose_meaningful_accessible_names() {
    let dir = scratch("names");
    let mut observed = probe("names", "controls", &dir);
    for (key, expected) in [
        ("emr", "EMR folder"),
        ("wave", "Wave folder"),
        ("processed", "Processed folder"),
        ("models", "Models directory"),
    ] {
        assert_eq!(observed.text(key), expected);
    }
    assert!(
        observed
            .text("question")
            .to_lowercase()
            .contains("question")
    );
    for label in [
        "Research",
        "Cases",
        "Signal",
        "Practice",
        "Rules",
        "Settings",
        "New question",
        "Reset",
        "Local AI",
        "Trace the evidence",
    ] {
        let key = label.replace(' ', "_").to_lowercase();
        assert_eq!(observed.text(&key), label, "name of {label}");
    }
    let tables = observed.text("tables");
    let mut tables: Vec<&str> = tables.lines().collect();
    tables.sort_unstable();
    assert_eq!(
        tables,
        [
            "Retrieved cases",
            "Source row at the selected minute",
            "Source rows behind the observation"
        ]
    );
    observed.finish();
}

#[test]
fn keyboard_focus_is_named_visible_and_recovers_after_warning() {
    let dir = scratch("focus");
    let mut observed = probe("focus", "keyboard", &dir);
    assert!(observed.number("tab_stops") >= 5);
    assert_eq!(observed.number("unnamed_stops"), 0);
    for control in ["model_menu", "trace", "rail", "table"] {
        assert!(
            observed.number(&format!("{control}_border_changes")) > 0,
            "{control} shows no focus"
        );
    }
    assert_eq!(observed.text("warning_focus_name"), "Research question");
    observed.finish();
}

#[test]
fn chart_has_text_and_shapes_beyond_color() {
    let dir = scratch("chart");
    let mut observed = probe("chart", "severity", &dir);
    assert_eq!(observed.text("name"), "Signal chart");
    let description = observed.text("description");
    for severity in ["info", "warn", "critical"] {
        assert!(description.contains(&format!("1 {severity} minutes")));
    }
    // The lane draws one shape per severity; the three differ, so color is not the only cue.
    let lane = observed.text("lane_markers");
    let markers: std::collections::BTreeMap<&str, &str> = lane
        .lines()
        .map(|row| {
            let (severity, marker) = row.split_once(':').unwrap();
            (severity, marker.split(':').next().unwrap())
        })
        .collect();
    assert_eq!(
        markers,
        [("info", "o"), ("warn", "^"), ("critical", "D")]
            .into_iter()
            .collect(),
        "lane shapes: circle = info, triangle = warn, diamond = critical"
    );
    assert_eq!(observed.number("lane_hatched_bands"), 3);
    // The design dropped the legend sentence: each row of the episode list carries the shape and the word.
    assert_eq!(observed.text("episode_list_name"), "Rule episodes");
    let rows = observed.text("episode_rows");
    assert_eq!(rows.lines().count(), 3);
    let mut glyphs = std::collections::BTreeSet::new();
    for row in rows.lines() {
        let fields: Vec<&str> = row.split('|').collect();
        let [severity, glyph, text, glyph_painted, word_painted, in_view] = fields[..] else {
            panic!("episode row: {row}")
        };
        let expected = match severity {
            "info" => "\u{25cb}",
            "warn" => "\u{25b3}",
            "critical" => "\u{25c6}",
            _ => panic!("unknown severity {severity}"),
        };
        assert_eq!(glyph, expected, "shape of {severity}");
        assert!(
            text.starts_with(glyph) && text.contains(severity),
            "row text lacks the shape or the word: {text}"
        );
        assert_eq!(glyph_painted, "1", "{severity} shape is not painted");
        assert_eq!(word_painted, "1", "{severity} word is not painted");
        assert_eq!(in_view, "1", "{severity} row is not visible");
        glyphs.insert(glyph);
    }
    assert_eq!(glyphs.len(), 3);
    observed.finish();
}

#[test]
fn navigation_and_question_card_fit_at_both_scales() {
    for variant in ["default", "scale2"] {
        let dir = scratch(variant);
        let mut observed = probe("layout", variant, &dir);
        for key in ["wizard_navigation", "settings_navigation", "question_card"] {
            let measurements = observed.text(key);
            assert!(!measurements.is_empty());
            for row in measurements.lines() {
                let dimensions: Vec<i32> =
                    row.split(',').map(|part| part.parse().unwrap()).collect();
                assert_eq!(dimensions.len(), 7);
                let [width, height, x, y, control_width, control_height, visible] = dimensions[..]
                else {
                    unreachable!()
                };
                assert_eq!(visible, 1, "hidden {key} at {variant}");
                assert!(control_width > 0 && control_height > 0);
                assert!(
                    x >= 0 && y >= 0 && x + control_width <= width && y + control_height <= height,
                    "clipped {key} at {variant}: {row}"
                );
            }
        }
        let _screen = observed.text("screen_geometry");
        let _window = observed.text("window_geometry");
        observed.finish();
    }
}

#[test]
fn startup_at_double_scale_fits_screen_and_keeps_board_usable() {
    let dir = scratch("startup-scale2");
    let mut observed = probe("startup_layout", "scale2", &dir);
    let screen_width = observed.number("screen_width");
    let screen_height = observed.number("screen_height");
    assert!(observed.number("window_width") <= screen_width);
    assert!(observed.number("window_height") <= screen_height);
    assert!(observed.number("board_height") >= 250);
    let names = observed.text("header_action_names");
    assert_eq!(
        names.lines().collect::<Vec<_>>(),
        [
            "Question scope",
            "Local AI",
            "New question",
            "Add comparison",
            "Trace the evidence"
        ]
    );
    let actions = observed.text("header_actions");
    assert_eq!(actions.lines().count(), 5);
    for row in actions.lines() {
        let bounds: Vec<i32> = row.split(',').map(|part| part.parse().unwrap()).collect();
        let [x, width, viewport_width] = bounds[..] else {
            panic!("header geometry")
        };
        assert!(
            x >= 0 && width > 0 && x + width <= viewport_width,
            "clipped header action: {row}"
        );
    }
    let reset = observed.text("reset_action");
    let bounds: Vec<i32> = reset.split(',').map(|part| part.parse().unwrap()).collect();
    let [x, width, panel_width, visible] = bounds[..] else {
        panic!("reset geometry")
    };
    assert!(
        visible == 1 && x >= 0 && width > 0 && x + width <= panel_width,
        "clipped Reset: {reset}"
    );
    assert!(
        !observed.boolean("board_sideways_scrollbar"),
        "the research board scrolls sideways"
    );
    let research = observed.text("research_line");
    assert!(
        research.to_lowercase().contains("not for patient care"),
        "research-only line: {research}"
    );
    assert!(
        observed.boolean("research_line_whole"),
        "research-only line is cut: {research}"
    );
    let practice = observed.text("practice_line");
    assert!(
        practice.starts_with("Fictional practice"),
        "fiction line: {practice}"
    );
    assert!(
        observed.boolean("practice_line_whole"),
        "fiction line is cut: {practice}"
    );
    assert!(
        observed.number("practice_window_width") <= screen_width,
        "a practice page widens the window past the screen"
    );
    let rail = observed.text("rail_buttons");
    assert_eq!(rail.lines().count(), 6);
    for row in rail.lines() {
        let bounds: Vec<i32> = row.split(',').map(|part| part.parse().unwrap()).collect();
        let [y, height, rail_height, visible] = bounds[..] else {
            panic!("rail geometry")
        };
        assert!(
            visible == 1 && y >= 0 && height > 0 && y + height <= rail_height,
            "clipped rail button: {row}"
        );
    }
    observed.finish();
}
