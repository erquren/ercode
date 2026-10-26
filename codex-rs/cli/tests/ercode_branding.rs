// ercode fork regression tests: branding must not depend on the installed filename.
use anyhow::Result;
use predicates::prelude::*;
use tempfile::TempDir;

fn ercode_command(home: &TempDir) -> Result<assert_cmd::Command> {
    let mut command = assert_cmd::Command::new(codex_utils_cargo_bin::cargo_bin("ercode")?);
    command.env("ERCODE_HOME", home.path());
    command.env("CODEX_HOME", home.path());
    Ok(command)
}

#[test]
fn help_and_version_use_ercode() -> Result<()> {
    let home = TempDir::new()?;
    ercode_command(&home)?
        .arg("--help")
        .assert()
        .success()
        .stdout(predicate::str::contains("Usage: ercode "))
        .stdout(predicate::str::contains("independent fork of OpenAI Codex"));
    ercode_command(&home)?
        .arg("--version")
        .assert()
        .success()
        .stdout(predicate::str::starts_with("ercode "));
    ercode_command(&home)?
        .args(["exec", "--help"])
        .assert()
        .success()
        .stdout(predicate::str::contains("Usage: ercode exec"));
    Ok(())
}

#[test]
fn shell_completions_target_ercode() -> Result<()> {
    let home = TempDir::new()?;
    for shell in ["bash", "zsh", "fish", "powershell"] {
        ercode_command(&home)?
            .args(["completion", shell])
            .assert()
            .success()
            .stdout(predicate::str::contains("ercode"));
    }
    Ok(())
}

#[test]
fn updates_cannot_install_the_upstream_product() -> Result<()> {
    let home = TempDir::new()?;
    ercode_command(&home)?
        .arg("update")
        .assert()
        .failure()
        .stderr(predicate::str::contains(
            "ercode does not use the official Codex updater",
        ));
    Ok(())
}

#[test]
fn daemon_updates_cannot_download_the_upstream_product() -> Result<()> {
    let home = TempDir::new()?;
    for command in ["update", "pid-update-loop"] {
        ercode_command(&home)?
            .args(["app-server", "daemon", command])
            .assert()
            .failure()
            .stderr(predicate::str::contains(
                "ercode disables official Codex daemon downloads",
            ));
    }
    Ok(())
}
