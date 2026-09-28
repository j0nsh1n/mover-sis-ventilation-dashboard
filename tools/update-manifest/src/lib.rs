use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use serde::Serialize;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

pub const KEY_ENV: &str = "MOVER_UPDATE_SIGNING_KEY_HEX";
pub const MAX_PACKAGE_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const MAX_MANIFEST_BYTES: usize = 64 * 1024;
const MAX_URL_LEN: usize = 2048;

#[derive(Debug)]
pub struct Error {
    message: String,
}

impl Error {
    fn msg(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Clone)]
pub struct SignRequest {
    pub version: String,
    pub linux: PathBuf,
    pub windows: PathBuf,
    pub base_url: String,
    pub out: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Package {
    pub url: String,
    pub sha256: String,
    pub size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReleaseManifest {
    pub version: String,
    pub linux: Package,
    pub windows: Package,
}

#[derive(Serialize)]
struct PackageJson {
    url: String,
    sha256: String,
    size: u64,
}

#[derive(Serialize)]
struct PackagesJson {
    #[serde(rename = "linux-x86_64")]
    linux: PackageJson,
    #[serde(rename = "windows-x86_64")]
    windows: PackageJson,
}

#[derive(Serialize)]
struct InnerJson {
    schema: u32,
    version: String,
    packages: PackagesJson,
}

#[derive(Serialize)]
struct EnvelopeJson {
    schema: u32,
    manifest: String,
    signature: String,
}

pub fn run<I, S>(args: I) -> Result<Option<String>>
where
    I: IntoIterator<Item = S>,
    S: Into<std::ffi::OsString>,
{
    let args: Vec<std::ffi::OsString> = args.into_iter().map(Into::into).collect();
    let Some(command) = args.first().and_then(|arg| arg.to_str()) else {
        return Err(Error::msg(usage()));
    };
    match command {
        "public-key" if args.len() == 1 => {
            let seed = seed_from_env()?;
            Ok(Some(public_key_hex(&seed)))
        }
        "sign" => {
            let request = parse_sign(&args[1..])?;
            let seed = seed_from_env()?;
            sign_release(&seed, &request)?;
            Ok(None)
        }
        _ => Err(Error::msg(usage())),
    }
}

fn usage() -> &'static str {
    "usage: mover-update-manifest public-key | sign --version x.y.z --linux PATH --windows PATH --base-url HTTPS_URL --out PATH"
}

pub fn seed_from_env() -> Result<[u8; 32]> {
    match std::env::var(KEY_ENV) {
        Ok(value) => decode_seed(&value),
        Err(std::env::VarError::NotPresent) => {
            Err(Error::msg("MOVER_UPDATE_SIGNING_KEY_HEX is not set"))
        }
        Err(std::env::VarError::NotUnicode(_)) => Err(Error::msg(
            "MOVER_UPDATE_SIGNING_KEY_HEX must be exactly 64 hex characters",
        )),
    }
}

pub fn decode_seed(hex: &str) -> Result<[u8; 32]> {
    decode_hex_bytes(hex, true)
        .map_err(|_| Error::msg("MOVER_UPDATE_SIGNING_KEY_HEX must be exactly 64 hex characters"))
}

pub fn public_key_hex(seed: &[u8; 32]) -> String {
    hex_encode(&public_key_bytes(seed))
}

pub fn public_key_bytes(seed: &[u8; 32]) -> [u8; 32] {
    SigningKey::from_bytes(seed).verifying_key().to_bytes()
}

pub fn sign_release(seed: &[u8; 32], request: &SignRequest) -> Result<()> {
    let document = build_signed_document(seed, request)?;
    let public_key = public_key_bytes(seed);
    verify_signed_manifest(&document, &public_key)?;
    write_atomic(&request.out, &document)
}

pub fn verify_signed_manifest(bytes: &[u8], public_key: &[u8; 32]) -> Result<ReleaseManifest> {
    if bytes.len() > MAX_MANIFEST_BYTES {
        return Err(Error::msg("signed manifest exceeds 64KiB"));
    }
    let value: Value =
        serde_json::from_slice(bytes).map_err(|_| Error::msg("signed manifest is not JSON"))?;
    let obj = value
        .as_object()
        .ok_or_else(|| Error::msg("signed manifest must be a JSON object"))?;
    require_exact_keys(obj, &["schema", "manifest", "signature"])?;
    let schema = json_u64(obj.get("schema"), "schema")?;
    if schema != 2 {
        return Err(Error::msg("signed manifest schema must be 2"));
    }
    let manifest = obj
        .get("manifest")
        .and_then(Value::as_str)
        .ok_or_else(|| Error::msg("manifest must be a string"))?;
    let signature_hex = obj
        .get("signature")
        .and_then(Value::as_str)
        .ok_or_else(|| Error::msg("signature must be lowercase hex"))?;
    let signature_bytes = decode_signature(signature_hex)?;
    let signature = Signature::try_from(signature_bytes.as_slice())
        .map_err(|_| Error::msg("signature is not a valid Ed25519 signature"))?;
    let verifying_key = VerifyingKey::from_bytes(public_key)
        .map_err(|_| Error::msg("public key is not a valid Ed25519 key"))?;
    verifying_key
        .verify(manifest.as_bytes(), &signature)
        .map_err(|_| Error::msg("signature does not match the manifest"))?;
    parse_inner_manifest(manifest)
}

fn build_signed_document(seed: &[u8; 32], request: &SignRequest) -> Result<Vec<u8>> {
    validate_version(&request.version)?;
    require_https(&request.base_url)?;
    if same_file(&request.linux, &request.windows) {
        return Err(Error::msg(
            "linux and windows packages must be different files",
        ));
    }
    if same_file(&request.out, &request.linux) || same_file(&request.out, &request.windows) {
        return Err(Error::msg("output path must not be a package file"));
    }
    let linux_name = package_filename(&request.linux)?;
    let windows_name = package_filename(&request.windows)?;
    if linux_name == windows_name {
        return Err(Error::msg("package filenames must be different"));
    }
    let linux_url = package_url(&request.base_url, &linux_name)?;
    let windows_url = package_url(&request.base_url, &windows_name)?;
    let (linux_sha, linux_size) = hash_package(&request.linux)?;
    let (windows_sha, windows_size) = hash_package(&request.windows)?;
    let inner = InnerJson {
        schema: 1,
        version: request.version.clone(),
        packages: PackagesJson {
            linux: PackageJson {
                url: linux_url,
                sha256: linux_sha,
                size: linux_size,
            },
            windows: PackageJson {
                url: windows_url,
                sha256: windows_sha,
                size: windows_size,
            },
        },
    };
    let manifest =
        serde_json::to_string(&inner).map_err(|_| Error::msg("failed to encode the manifest"))?;
    if manifest.len() > MAX_MANIFEST_BYTES {
        return Err(Error::msg("manifest exceeds 64KiB"));
    }
    let signature = SigningKey::from_bytes(seed).sign(manifest.as_bytes());
    let envelope = EnvelopeJson {
        schema: 2,
        manifest,
        signature: hex_encode(&signature.to_bytes()),
    };
    let mut document = serde_json::to_vec(&envelope)
        .map_err(|_| Error::msg("failed to encode the signed manifest"))?;
    document.push(b'\n');
    if document.len() > MAX_MANIFEST_BYTES {
        return Err(Error::msg("signed manifest exceeds 64KiB"));
    }
    Ok(document)
}

fn parse_sign(args: &[std::ffi::OsString]) -> Result<SignRequest> {
    let mut version = None;
    let mut linux = None;
    let mut windows = None;
    let mut base_url = None;
    let mut out = None;
    let mut index = 0;
    while index < args.len() {
        let flag = args[index].to_str().ok_or_else(|| Error::msg(usage()))?;
        let (name, inline) = flag
            .split_once('=')
            .map(|(name, value)| (name, Some(value)))
            .unwrap_or((flag, None));
        let value = match inline {
            Some(value) => {
                index += 1;
                value.to_string()
            }
            None => {
                index += 1;
                let raw = args.get(index).ok_or_else(|| Error::msg(usage()))?;
                index += 1;
                raw.to_str()
                    .ok_or_else(|| Error::msg("arguments must be UTF-8"))?
                    .to_string()
            }
        };
        let slot = match name {
            "--version" => &mut version,
            "--linux" => &mut linux,
            "--windows" => &mut windows,
            "--base-url" => &mut base_url,
            "--out" => &mut out,
            _ => return Err(Error::msg(usage())),
        };
        if slot.is_some() {
            return Err(Error::msg(format!("duplicate {name}")));
        }
        *slot = Some(value);
    }
    Ok(SignRequest {
        version: version.ok_or_else(|| Error::msg(usage()))?,
        linux: PathBuf::from(linux.ok_or_else(|| Error::msg(usage()))?),
        windows: PathBuf::from(windows.ok_or_else(|| Error::msg(usage()))?),
        base_url: base_url.ok_or_else(|| Error::msg(usage()))?,
        out: PathBuf::from(out.ok_or_else(|| Error::msg(usage()))?),
    })
}

fn parse_inner_manifest(manifest: &str) -> Result<ReleaseManifest> {
    if manifest.len() > MAX_MANIFEST_BYTES {
        return Err(Error::msg("manifest exceeds 64KiB"));
    }
    let value: Value =
        serde_json::from_str(manifest).map_err(|_| Error::msg("manifest is not JSON"))?;
    let obj = value
        .as_object()
        .ok_or_else(|| Error::msg("manifest must be a JSON object"))?;
    require_exact_keys(obj, &["schema", "version", "packages"])?;
    let schema = json_u64(obj.get("schema"), "schema")?;
    if schema != 1 {
        return Err(Error::msg("manifest schema must be 1"));
    }
    let version = obj
        .get("version")
        .and_then(Value::as_str)
        .ok_or_else(|| Error::msg("manifest version must be a string"))?;
    validate_version(version)?;
    let packages = obj
        .get("packages")
        .and_then(Value::as_object)
        .ok_or_else(|| Error::msg("manifest packages must be an object"))?;
    require_exact_keys(packages, &["linux-x86_64", "windows-x86_64"])?;
    Ok(ReleaseManifest {
        version: version.to_string(),
        linux: parse_package(packages.get("linux-x86_64"))?,
        windows: parse_package(packages.get("windows-x86_64"))?,
    })
}

fn parse_package(value: Option<&Value>) -> Result<Package> {
    let obj = value
        .and_then(Value::as_object)
        .ok_or_else(|| Error::msg("package must be an object"))?;
    require_exact_keys(obj, &["url", "sha256", "size"])?;
    let url = obj
        .get("url")
        .and_then(Value::as_str)
        .ok_or_else(|| Error::msg("package url must be a string"))?
        .to_string();
    require_https(&url)?;
    let sha256 = obj
        .get("sha256")
        .and_then(Value::as_str)
        .ok_or_else(|| Error::msg("package sha256 must be lowercase hex"))?;
    if decode_hex_bytes::<32>(sha256, false).is_err() {
        return Err(Error::msg(
            "package sha256 must be 64 lowercase hex characters",
        ));
    }
    let size = json_u64(obj.get("size"), "size")?;
    if size == 0 || size > MAX_PACKAGE_BYTES {
        return Err(Error::msg("package size is missing or above the 2GiB cap"));
    }
    Ok(Package {
        url,
        sha256: sha256.to_string(),
        size,
    })
}

fn validate_version(version: &str) -> Result<()> {
    let mut parts = version.split('.');
    let ok = (0..3).all(|_| match parts.next() {
        Some(part) => {
            !part.is_empty()
                && part.bytes().all(|byte| byte.is_ascii_digit())
                && (part == "0" || !part.starts_with('0'))
        }
        None => false,
    }) && parts.next().is_none();
    if !ok {
        return Err(Error::msg("version must be numeric semver x.y.z"));
    }
    Ok(())
}

fn require_https(url: &str) -> Result<()> {
    if url.len() > MAX_URL_LEN || !url.is_ascii() {
        return Err(Error::msg(
            "URL must be https and must not carry credentials",
        ));
    }
    let Some(rest) = url.strip_prefix("https://") else {
        return Err(Error::msg(
            "URL must be https and must not carry credentials",
        ));
    };
    if rest.is_empty()
        || rest.ends_with('/')
        || rest.contains(['?', '#', '\\', ' ', '@'])
        || rest.bytes().any(|byte| byte.is_ascii_control())
    {
        return Err(Error::msg(
            "URL must be https, have no credentials or query, and must not end with /",
        ));
    }
    let (authority, path) = match rest.split_once('/') {
        Some((authority, path)) => (authority, path),
        None => (rest, ""),
    };
    validate_authority(authority)?;
    if !path.is_empty()
        && path
            .split('/')
            .any(|segment| segment.is_empty() || segment == "." || segment == "..")
    {
        return Err(Error::msg(
            "URL path must not contain empty, . or .. segments",
        ));
    }
    Ok(())
}

fn validate_authority(authority: &str) -> Result<()> {
    let bracketed = authority.starts_with('[');
    let (host, port) = split_host_port(authority)?;
    if host.is_empty() || host.ends_with('.') || host.starts_with('.') {
        return Err(Error::msg("URL host is missing or ends with '.'"));
    }
    let host_ok = if bracketed {
        true
    } else if host.contains(':') {
        false
    } else {
        host.chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '.' || ch == '-')
            && host
                .split('.')
                .all(|label| !label.is_empty() && !label.starts_with('-') && !label.ends_with('-'))
    };
    if !host_ok {
        return Err(Error::msg("URL host is missing or ends with '.'"));
    }
    if let Some(port) = port {
        match port.parse::<u16>() {
            Ok(value) if value > 0 => {}
            _ => return Err(Error::msg("URL port is invalid")),
        }
    }
    Ok(())
}

fn split_host_port(authority: &str) -> Result<(&str, Option<&str>)> {
    if authority.is_empty() {
        return Err(Error::msg("URL host is missing or ends with '.'"));
    }
    if let Some(rest) = authority.strip_prefix('[') {
        let Some((host, after)) = rest.split_once(']') else {
            return Err(Error::msg("URL host is missing or ends with '.'"));
        };
        if host.is_empty()
            || host.ends_with('.')
            || !host
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() || byte == b':' || byte == b'.')
        {
            return Err(Error::msg("URL host is missing or ends with '.'"));
        }
        if after.is_empty() {
            return Ok((host, None));
        }
        let Some(port) = after.strip_prefix(':') else {
            return Err(Error::msg("URL port is invalid"));
        };
        if port.is_empty() {
            return Err(Error::msg("URL port is invalid"));
        }
        return Ok((host, Some(port)));
    }
    match authority.rsplit_once(':') {
        Some((host, port))
            if !port.is_empty() && port.bytes().all(|byte| byte.is_ascii_digit()) =>
        {
            Ok((host, Some(port)))
        }
        Some(_) => Err(Error::msg("URL port is invalid")),
        None => Ok((authority, None)),
    }
}

fn package_url(base_url: &str, filename: &str) -> Result<String> {
    let url = format!("{base_url}/{filename}");
    require_https(&url)?;
    Ok(url)
}

fn package_filename(path: &Path) -> Result<String> {
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| Error::msg("package filename must be a single URL path segment"))?;
    if name.is_empty()
        || name.starts_with('.')
        || !name
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '-'))
    {
        return Err(Error::msg(
            "package filename must be a single URL path segment",
        ));
    }
    Ok(name.to_string())
}

fn hash_package(path: &Path) -> Result<(String, u64)> {
    let file = File::open(path).map_err(|err| io_error(path, err))?;
    let meta = file.metadata().map_err(|err| io_error(path, err))?;
    if !meta.is_file() {
        return Err(Error::msg(format!(
            "{} is not a regular file",
            path.display()
        )));
    }
    hash_reader(file, meta.len(), path)
}

fn hash_reader(mut reader: impl Read, declared: u64, path: &Path) -> Result<(String, u64)> {
    if declared == 0 {
        return Err(Error::msg(format!("{} is empty", path.display())));
    }
    if declared > MAX_PACKAGE_BYTES {
        return Err(Error::msg(format!(
            "{} is above the 2GiB cap",
            path.display()
        )));
    }
    let mut hasher = Sha256::new();
    let mut read_len = 0u64;
    let mut buf = [0u8; 64 * 1024];
    loop {
        let n = reader.read(&mut buf).map_err(|err| io_error(path, err))?;
        if n == 0 {
            break;
        }
        read_len = read_len.saturating_add(u64::try_from(n).unwrap_or(u64::MAX));
        if read_len > declared || read_len > MAX_PACKAGE_BYTES {
            return Err(Error::msg(format!(
                "{} changed while hashing",
                path.display()
            )));
        }
        hasher.update(&buf[..n]);
    }
    if read_len != declared {
        return Err(Error::msg(format!(
            "{} changed while hashing",
            path.display()
        )));
    }
    Ok((hex_encode(hasher.finalize().as_slice()), read_len))
}

fn write_atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    if path.as_os_str().is_empty() {
        return Err(Error::msg("output path is empty"));
    }
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty());
    let parent = parent.unwrap_or_else(|| Path::new("."));
    if !parent.is_dir() {
        return Err(Error::msg(format!(
            "output directory {} does not exist",
            parent.display()
        )));
    }
    let file_name = path
        .file_name()
        .ok_or_else(|| Error::msg("output path must be a file"))?;
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    let mut tmp_name = std::ffi::OsString::from(".");
    tmp_name.push(file_name);
    tmp_name.push(format!(".{}.{nanos}.tmp", std::process::id()));
    let tmp_path = parent.join(tmp_name);
    let write_result = (|| -> Result<()> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp_path)
            .map_err(|err| io_error(&tmp_path, err))?;
        file.write_all(bytes)
            .map_err(|err| io_error(&tmp_path, err))?;
        file.sync_all().map_err(|err| io_error(&tmp_path, err))?;
        Ok(())
    })();
    if let Err(err) = write_result {
        let _ = fs::remove_file(&tmp_path);
        return Err(err);
    }
    if let Err(err) = fs::rename(&tmp_path, path) {
        let _ = fs::remove_file(&tmp_path);
        return Err(io_error(path, err));
    }
    Ok(())
}

fn same_file(left: &Path, right: &Path) -> bool {
    if left == right {
        return true;
    }
    match (fs::canonicalize(left), fs::canonicalize(right)) {
        (Ok(left), Ok(right)) => left == right,
        _ => false,
    }
}

fn require_exact_keys(obj: &Map<String, Value>, keys: &[&str]) -> Result<()> {
    if obj.len() == keys.len() && keys.iter().all(|key| obj.contains_key(*key)) {
        Ok(())
    } else {
        Err(Error::msg(
            "JSON object does not contain the expected fields",
        ))
    }
}

fn json_u64(value: Option<&Value>, field: &str) -> Result<u64> {
    let Some(Value::Number(number)) = value else {
        return Err(Error::msg(format!("{field} must be an integer")));
    };
    if number.is_u64() {
        number
            .as_u64()
            .ok_or_else(|| Error::msg(format!("{field} must be an integer")))
    } else {
        Err(Error::msg(format!("{field} must be an integer")))
    }
}

fn decode_hex_bytes<const N: usize>(
    hex: &str,
    allow_upper: bool,
) -> std::result::Result<[u8; N], ()> {
    if hex.len() != N * 2 {
        return Err(());
    }
    let mut out = [0u8; N];
    for (index, byte) in out.iter_mut().enumerate() {
        let hi = hex_nibble(hex.as_bytes()[index * 2], allow_upper)?;
        let lo = hex_nibble(hex.as_bytes()[index * 2 + 1], allow_upper)?;
        *byte = (hi << 4) | lo;
    }
    Ok(out)
}

fn hex_nibble(byte: u8, allow_upper: bool) -> std::result::Result<u8, ()> {
    match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        b'A'..=b'F' if allow_upper => Ok(byte - b'A' + 10),
        _ => Err(()),
    }
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

fn io_error(path: &Path, err: io::Error) -> Error {
    Error::msg(format!("{}: {err}", path.display()))
}

fn decode_signature(hex: &str) -> Result<[u8; 64]> {
    decode_hex_bytes(hex, false)
        .map_err(|_| Error::msg("signature must be 128 lowercase hex characters"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    const RFC_SEED: &str = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60";
    const RFC_PUBLIC: &str = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a";
    const RFC_EMPTY_SIG: &str = "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b";

    #[test]
    fn rfc8032_seed_signs_empty_message() {
        let seed = decode_seed(RFC_SEED).unwrap();
        assert_eq!(public_key_hex(&seed), RFC_PUBLIC);
        let signature = SigningKey::from_bytes(&seed).sign(b"");
        assert_eq!(hex_encode(&signature.to_bytes()), RFC_EMPTY_SIG);
    }

    #[test]
    fn rejects_bad_versions_and_urls() {
        assert!(validate_version("0.8.0").is_ok());
        assert!(validate_version("01.2.3").is_err());
        assert!(validate_version("1.2").is_err());
        assert!(validate_version("1.2.3-rc1").is_err());
        assert!(require_https("https://updates.example.com/mover/v0.8.0").is_ok());
        assert!(require_https("https://updates.example.com").is_ok());
        assert!(require_https("https://example.com.").is_err());
        assert!(require_https("https://user:pass@example.com/pkg").is_err());
        assert!(require_https("http://example.com/pkg").is_err());
        assert!(require_https("https://example.com/pkg/").is_err());
        assert!(require_https("https://example.com/pkg?x=1").is_err());
    }

    #[test]
    fn hash_rejects_empty_and_over_cap_without_reading() {
        let path = Path::new("package.bin");
        let err = hash_reader(Cursor::new(b""), 0, path).unwrap_err();
        assert!(err.to_string().contains("empty"));
        let err = hash_reader(Cursor::new([0u8; 1]), MAX_PACKAGE_BYTES + 1, path).unwrap_err();
        assert!(err.to_string().contains("2GiB"));
        let (digest, size) = hash_reader(Cursor::new(b"hello"), 5, path).unwrap();
        assert_eq!(size, 5);
        assert_eq!(
            digest,
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        );
    }

    #[test]
    fn signed_manifest_matches_schema_and_rejects_tampering() {
        let dir = temp_dir();
        let linux = dir.0.join("linux.tar.gz");
        let windows = dir.0.join("windows.zip");
        fs::write(&linux, b"hello").unwrap();
        fs::write(&windows, b"world").unwrap();
        let out = dir.0.join("manifest.json");
        let seed = decode_seed(RFC_SEED).unwrap();
        sign_release(
            &seed,
            &SignRequest {
                version: "0.8.0".to_string(),
                linux: linux.clone(),
                windows: windows.clone(),
                base_url: "https://updates.example.com/mover/v0.8.0".to_string(),
                out: out.clone(),
            },
        )
        .unwrap();
        let bytes = fs::read(&out).unwrap();
        let parsed = verify_signed_manifest(&bytes, &public_key_bytes(&seed)).unwrap();
        assert_eq!(parsed.version, "0.8.0");
        assert_eq!(
            parsed.linux.url,
            "https://updates.example.com/mover/v0.8.0/linux.tar.gz"
        );
        assert_eq!(parsed.linux.size, 5);
        assert_eq!(
            parsed.linux.sha256,
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        );
        assert_eq!(parsed.windows.size, 5);
        let envelope: Value = serde_json::from_slice(&bytes).unwrap();
        let manifest = envelope["manifest"].as_str().unwrap();
        assert_eq!(
            manifest,
            r#"{"schema":1,"version":"0.8.0","packages":{"linux-x86_64":{"url":"https://updates.example.com/mover/v0.8.0/linux.tar.gz","sha256":"2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824","size":5},"windows-x86_64":{"url":"https://updates.example.com/mover/v0.8.0/windows.zip","sha256":"486ea46224d1bb4fb680f34f7c9ad96a8f24ec88be73ea8e5a6c65260e9cb8a7","size":5}}}"#
        );
        assert!(verifying_key_rejects_envelope_bytes(&bytes, &seed));

        let mut tampered_payload = envelope.clone();
        tampered_payload["manifest"] = Value::String(manifest.replace("0.8.0", "0.8.1"));
        let tampered_payload = serde_json::to_vec(&tampered_payload).unwrap();
        assert!(verify_signed_manifest(&tampered_payload, &public_key_bytes(&seed)).is_err());

        let mut tampered_sig = envelope.clone();
        let signature = tampered_sig["signature"].as_str().unwrap().to_string();
        let flipped = flip_last_hex(&signature);
        tampered_sig["signature"] = Value::String(flipped);
        let tampered_sig = serde_json::to_vec(&tampered_sig).unwrap();
        let err = verify_signed_manifest(&tampered_sig, &public_key_bytes(&seed)).unwrap_err();
        assert!(err.to_string().contains("signature"));
    }

    #[test]
    fn failed_sign_leaves_no_partial_output() {
        let dir = temp_dir();
        let linux = dir.0.join("linux.tar.gz");
        let windows = dir.0.join("windows.zip");
        fs::write(&windows, b"world").unwrap();
        let out = dir.0.join("manifest.json");
        fs::write(&out, b"previous").unwrap();
        let err = sign_release(
            &decode_seed(RFC_SEED).unwrap(),
            &SignRequest {
                version: "0.8.0".to_string(),
                linux,
                windows,
                base_url: "https://updates.example.com/mover".to_string(),
                out: out.clone(),
            },
        )
        .unwrap_err();
        assert!(err.to_string().contains("linux.tar.gz"));
        assert_eq!(fs::read(&out).unwrap(), b"previous");
        assert!(dir.0.read_dir().unwrap().all(|entry| {
            !entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .ends_with(".tmp")
        }));
    }

    fn verifying_key_rejects_envelope_bytes(bytes: &[u8], seed: &[u8; 32]) -> bool {
        let envelope: Value = serde_json::from_slice(bytes).unwrap();
        let signature = decode_signature(envelope["signature"].as_str().unwrap()).unwrap();
        let signature = Signature::try_from(signature.as_slice()).unwrap();
        let key = VerifyingKey::from_bytes(&public_key_bytes(seed)).unwrap();
        key.verify(bytes, &signature).is_err()
    }

    fn flip_last_hex(hex: &str) -> String {
        let mut chars: Vec<char> = hex.chars().collect();
        let last = chars.len() - 1;
        chars[last] = if chars[last] == '0' { '1' } else { '0' };
        chars.into_iter().collect()
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
            std::env::temp_dir().join(format!("mover-manifest-{}.{nanos}", std::process::id()));
        fs::create_dir(&path).unwrap();
        TempDir(path)
    }
}
