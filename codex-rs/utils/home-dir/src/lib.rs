// Modified for ercode: isolated default home with explicit Codex compatibility.
use codex_utils_absolute_path::AbsolutePathBuf;
use dirs::home_dir;
use std::path::PathBuf;

/// Returns the ercode configuration directory, using `ERCODE_HOME`, then the
/// explicitly configured compatibility override `CODEX_HOME`, then `~/.ercode`.
/// Empty overrides are ignored. An explicit override must exist and be a
/// directory; an invalid primary override never falls back to another home.
/// The default directory need not exist. Old `~/.codex` data is not read or moved.
pub fn find_codex_home() -> std::io::Result<AbsolutePathBuf> {
    let ercode_home_env = read_home_override("ERCODE_HOME", std::env::var("ERCODE_HOME"))?;
    let codex_home_env = if ercode_home_env
        .as_ref()
        .is_some_and(|value| !value.is_empty())
    {
        None
    } else {
        read_home_override("CODEX_HOME", std::env::var("CODEX_HOME"))?
    };
    find_codex_home_from_env(
        ercode_home_env.as_deref(),
        codex_home_env.as_deref(),
        home_dir(),
    )
}

fn read_home_override(
    variable: &str,
    value: Result<String, std::env::VarError>,
) -> std::io::Result<Option<String>> {
    match value {
        Ok(value) => Ok(Some(value)),
        Err(std::env::VarError::NotPresent) => Ok(None),
        Err(std::env::VarError::NotUnicode(_)) => Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("{variable} must contain a Unicode path; refusing to fall back"),
        )),
    }
}

fn find_codex_home_from_env(
    ercode_home_env: Option<&str>,
    codex_home_env: Option<&str>,
    default_home: Option<PathBuf>,
) -> std::io::Result<AbsolutePathBuf> {
    let selected = ercode_home_env
        .filter(|value| !value.is_empty())
        .map(|value| ("ERCODE_HOME", value))
        .or_else(|| {
            codex_home_env
                .filter(|value| !value.is_empty())
                .map(|value| ("CODEX_HOME", value))
        });
    match selected {
        Some((variable, val)) => {
            let path = PathBuf::from(val);
            let metadata = std::fs::metadata(&path).map_err(|err| match err.kind() {
                std::io::ErrorKind::NotFound => std::io::Error::new(
                    std::io::ErrorKind::NotFound,
                    format!("{variable} points to {val:?}, but that path does not exist"),
                ),
                _ => std::io::Error::new(
                    err.kind(),
                    format!("failed to read {variable} {val:?}: {err}"),
                ),
            })?;

            if !metadata.is_dir() {
                Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    format!("{variable} points to {val:?}, but that path is not a directory"),
                ))
            } else {
                let canonical = path.canonicalize().map_err(|err| {
                    std::io::Error::new(
                        err.kind(),
                        format!("failed to canonicalize {variable} {val:?}: {err}"),
                    )
                })?;
                AbsolutePathBuf::from_absolute_path(canonical)
            }
        }
        None => {
            let mut p = default_home.ok_or_else(|| {
                std::io::Error::new(
                    std::io::ErrorKind::NotFound,
                    "Could not find home directory",
                )
            })?;
            p.push(".ercode");
            AbsolutePathBuf::from_absolute_path(p)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::find_codex_home_from_env;
    use codex_utils_absolute_path::AbsolutePathBuf;
    use pretty_assertions::assert_eq;
    use std::fs;
    use std::io::ErrorKind;
    use tempfile::TempDir;

    #[test]
    fn find_codex_home_env_missing_path_is_fatal() {
        let temp_home = TempDir::new().expect("temp home");
        let missing = temp_home.path().join("missing-codex-home");
        let missing_str = missing
            .to_str()
            .expect("missing codex home path should be valid utf-8");

        let err = find_codex_home_from_env(None, Some(missing_str), None)
            .expect_err("missing CODEX_HOME");
        assert_eq!(err.kind(), ErrorKind::NotFound);
        assert!(
            err.to_string().contains("CODEX_HOME"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn find_codex_home_env_file_path_is_fatal() {
        let temp_home = TempDir::new().expect("temp home");
        let file_path = temp_home.path().join("codex-home.txt");
        fs::write(&file_path, "not a directory").expect("write temp file");
        let file_str = file_path
            .to_str()
            .expect("file codex home path should be valid utf-8");

        let err =
            find_codex_home_from_env(None, Some(file_str), None).expect_err("file CODEX_HOME");
        assert_eq!(err.kind(), ErrorKind::InvalidInput);
        assert!(
            err.to_string().contains("not a directory"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn find_codex_home_env_valid_directory_canonicalizes() {
        let temp_home = TempDir::new().expect("temp home");
        let temp_str = temp_home
            .path()
            .to_str()
            .expect("temp codex home path should be valid utf-8");

        let resolved =
            find_codex_home_from_env(None, Some(temp_str), None).expect("valid CODEX_HOME");
        let expected = temp_home
            .path()
            .canonicalize()
            .expect("canonicalize temp home");
        let expected = AbsolutePathBuf::from_absolute_path(expected).expect("absolute home");
        assert_eq!(resolved, expected);
    }

    #[cfg(unix)]
    #[test]
    fn non_unicode_primary_override_fails_closed() {
        use std::os::unix::ffi::OsStringExt;
        let value = std::ffi::OsString::from_vec(vec![0xff]);
        let err =
            super::read_home_override("ERCODE_HOME", Err(std::env::VarError::NotUnicode(value)))
                .unwrap_err();
        assert_eq!(err.kind(), ErrorKind::InvalidInput);
        assert!(err.to_string().contains("ERCODE_HOME"));
        assert!(err.to_string().contains("refusing to fall back"));
    }

    #[test]
    fn default_home_is_isolated_from_existing_codex_data() {
        let home = TempDir::new().expect("synthetic home");
        fs::create_dir(home.path().join(".codex")).unwrap();
        fs::write(
            home.path().join(".codex/auth.json"),
            "old fixture credentials",
        )
        .unwrap();
        let resolved = find_codex_home_from_env(None, None, Some(home.path().into())).unwrap();
        assert_eq!(resolved.as_path(), home.path().join(".ercode"));
        assert!(!resolved.as_path().exists());
        assert!(home.path().join(".codex/auth.json").is_file());
    }

    #[test]
    fn ercode_home_takes_precedence_over_explicit_codex_home() {
        let ercode = TempDir::new().unwrap();
        let codex = TempDir::new().unwrap();
        let resolved =
            find_codex_home_from_env(ercode.path().to_str(), codex.path().to_str(), None).unwrap();
        assert_eq!(resolved.as_path(), ercode.path().canonicalize().unwrap());
    }

    #[test]
    fn invalid_ercode_home_does_not_fall_back_to_codex_home() {
        let codex = TempDir::new().unwrap();
        let missing = codex.path().join("missing");
        let err = find_codex_home_from_env(missing.to_str(), codex.path().to_str(), None)
            .expect_err("primary override must not silently fall back");
        assert_eq!(err.kind(), ErrorKind::NotFound);
        assert!(err.to_string().contains("ERCODE_HOME"));
    }

    #[test]
    fn empty_primary_override_preserves_explicit_legacy_compatibility() {
        let codex = TempDir::new().unwrap();
        let resolved = find_codex_home_from_env(Some(""), codex.path().to_str(), None).unwrap();
        assert_eq!(resolved.as_path(), codex.path().canonicalize().unwrap());
    }

    #[test]
    fn empty_overrides_use_the_new_default() {
        let home = TempDir::new().unwrap();
        let resolved =
            find_codex_home_from_env(Some(""), Some(""), Some(home.path().into())).unwrap();
        assert_eq!(resolved.as_path(), home.path().join(".ercode"));
    }
}
