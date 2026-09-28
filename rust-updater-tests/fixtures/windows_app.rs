use std::env;
use std::fs;
use std::thread;
use std::time::Duration;

fn main() {
    let install = env::current_exe().unwrap().parent().unwrap().to_path_buf();
    let marker = fs::read_to_string(install.join("marker")).unwrap();
    fs::write(env::var("MOVER_TEST_STAMP").unwrap(), &marker).unwrap();
    if marker.trim() != "new-fail" {
        thread::sleep(Duration::from_secs(6));
    }
}
