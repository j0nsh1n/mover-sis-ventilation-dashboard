use std::collections::BTreeMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub fn scratch(label: &str) -> PathBuf {
    let base = std::env::var_os("MOVER_CONTRACT_SCRATCH")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").expect("HOME or MOVER_CONTRACT_SCRATCH"))
                .join("Projects/medical/mover/_work/appimage-fix-first/contracts")
        });
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let path = base.join(format!("{label}-{}-{nonce}", std::process::id()));
    fs::create_dir_all(&path).unwrap();
    path
}

fn drain(mut pipe: impl Read) -> (Vec<u8>, bool) {
    let mut kept = Vec::new();
    let mut bytes = [0; 8192];
    let mut exceeded = false;
    loop {
        let count = pipe.read(&mut bytes).expect("read child pipe");
        if count == 0 {
            break;
        }
        let available = (1024 * 1024_usize).saturating_sub(kept.len());
        kept.extend_from_slice(&bytes[..count.min(available)]);
        exceeded |= count > available;
    }
    (kept, exceeded)
}

pub fn execute(command: &mut Command, directory: &Path, label: &str) -> Vec<u8> {
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("start bounded child");
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let out_reader = thread::spawn(move || drain(stdout));
    let err_reader = thread::spawn(move || drain(stderr));
    let deadline = Instant::now() + Duration::from_secs(45);
    let mut timed_out = false;
    let status = loop {
        if let Some(status) = child.try_wait().unwrap() {
            break status;
        }
        if Instant::now() >= deadline {
            timed_out = true;
            child.kill().expect("kill owned leaf child");
            break child.wait().unwrap();
        }
        thread::sleep(Duration::from_millis(10));
    };
    let (out, out_exceeded) = out_reader.join().unwrap();
    let (err, err_exceeded) = err_reader.join().unwrap();
    fs::write(directory.join(format!("{label}.stdout")), &out).unwrap();
    fs::write(directory.join(format!("{label}.stderr")), &err).unwrap();
    assert!(
        !timed_out,
        "{label} exceeded 45s; diagnostics at {}",
        directory.display()
    );
    assert!(
        !out_exceeded && !err_exceeded,
        "{label} exceeded output limit"
    );
    assert!(
        status.success(),
        "{label} exited {status}; diagnostics at {}\n{}",
        directory.display(),
        String::from_utf8_lossy(&err)
    );
    out
}

#[derive(Debug)]
enum Value {
    Text(String),
    Number(u64),
    Boolean(bool),
}

pub struct Observation(BTreeMap<String, Value>);

impl Observation {
    fn parse(bytes: Vec<u8>) -> Self {
        let mut values = BTreeMap::new();
        for line in String::from_utf8(bytes).expect("UTF-8 protocol").lines() {
            let (key, encoded) = line.split_once('=').expect("key=value field");
            assert!(!key.is_empty() && key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_'));
            let (kind, value) = encoded.split_once(':').expect("typed field");
            let parsed = match kind {
                "t" => {
                    assert!(value.len().is_multiple_of(2), "even hex length");
                    let decoded = value
                        .as_bytes()
                        .as_chunks::<2>()
                        .0
                        .iter()
                        .map(|pair| {
                            let pair = std::str::from_utf8(pair).unwrap();
                            u8::from_str_radix(pair, 16).expect("hex text")
                        })
                        .collect();
                    Value::Text(String::from_utf8(decoded).expect("UTF-8 text"))
                }
                "u" => Value::Number(value.parse().expect("unsigned integer")),
                "b" => Value::Boolean(match value {
                    "0" => false,
                    "1" => true,
                    _ => panic!("boolean field"),
                }),
                _ => panic!("unknown observation type"),
            };
            assert!(
                values.insert(key.to_owned(), parsed).is_none(),
                "duplicate {key}"
            );
        }
        let mut result = Self(values);
        assert_eq!(result.number("protocol"), 1);
        result
    }
    pub fn text(&mut self, key: &str) -> String {
        match self.0.remove(key).expect(key) {
            Value::Text(value) => value,
            _ => panic!("{key}: expected text"),
        }
    }
    pub fn number(&mut self, key: &str) -> u64 {
        match self.0.remove(key).expect(key) {
            Value::Number(value) => value,
            _ => panic!("{key}: expected number"),
        }
    }
    pub fn boolean(&mut self, key: &str) -> bool {
        match self.0.remove(key).expect(key) {
            Value::Boolean(value) => value,
            _ => panic!("{key}: expected boolean"),
        }
    }
    pub fn finish(self) {
        assert!(self.0.is_empty(), "unexpected fields {:?}", self.0);
    }
}

pub fn probe(operation: &str, variant: &str, directory: &Path) -> Observation {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let source = std::env::var_os("MOVER_SOURCE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| manifest.parent().unwrap().to_path_buf());
    let python = std::env::var_os("PYTHON")
        .unwrap_or_else(|| source.join(".venv/bin/python").into_os_string());
    let mut command = Command::new(python);
    command
        .arg(manifest.join("probe.py"))
        .args([operation, variant])
        .arg(directory)
        .current_dir(&source)
        .env("PYTHONPATH", &source)
        .env("QT_QPA_PLATFORM", "offscreen")
        .env(
            "QT_SCALE_FACTOR",
            if variant == "scale2" { "2" } else { "1" },
        )
        .env("MOVER_CONFIG_DIR", directory.join("config"))
        .env("MOVER_PROCESSED_DIR", directory.join("processed"))
        .env("MOVER_EMR_DIR", directory.join("source"))
        .env("XDG_DATA_HOME", directory.join("user-data"))
        .env("XDG_CACHE_HOME", directory.join("cache"))
        .env("TMPDIR", directory.join("tmp"));
    fs::create_dir_all(directory.join("tmp")).unwrap();
    Observation::parse(execute(
        &mut command,
        directory,
        &format!("{operation}-{variant}"),
    ))
}

pub fn files(path: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
    fn visit(base: &Path, path: &Path, result: &mut BTreeMap<PathBuf, Vec<u8>>) {
        for entry in fs::read_dir(path).unwrap() {
            let entry = entry.unwrap();
            if entry.file_type().unwrap().is_dir() {
                visit(base, &entry.path(), result);
            } else {
                result.insert(
                    entry.path().strip_prefix(base).unwrap().to_path_buf(),
                    fs::read(entry.path()).unwrap(),
                );
            }
        }
    }
    let mut result = BTreeMap::new();
    visit(path, path, &mut result);
    result
}
