use std::env;
use std::process::ExitCode;

fn main() -> ExitCode {
    match mover_update_manifest::run(env::args_os().skip(1)) {
        Ok(output) => {
            if let Some(text) = output {
                println!("{text}");
            }
            ExitCode::SUCCESS
        }
        Err(err) => {
            eprintln!("error: {err}");
            ExitCode::from(1)
        }
    }
}
