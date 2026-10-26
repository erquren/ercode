// Modified for ercode: explicit Responses-compatible endpoint setup without a connection probe.
//! Keys are masked and sent only to the existing credential-storage request, never config edits.

use super::*;
use crate::config_update::replace_config_value;
use codex_app_server_protocol::ConfigBatchWriteParams;

impl Default for ApiKeyInputState {
    fn default() -> Self {
        Self {
            base_url: "https://api.openai.com/v1".into(),
            http_consent: String::new(),
            consented_endpoint: None,
            model: String::new(),
            value: String::new(),
            field: ApiSetupField::BaseUrl,
            saving: false,
        }
    }
}

impl ApiKeyInputState {
    pub(crate) fn for_config(config: &crate::legacy_core::config::Config) -> Self {
        Self {
            base_url: config
                .model_provider
                .base_url
                .clone()
                .unwrap_or_else(|| "https://api.openai.com/v1".into()),
            model: config.model.clone().unwrap_or_default(),
            ..Self::default()
        }
    }

    fn current_value_mut(&mut self) -> &mut String {
        match self.field {
            ApiSetupField::BaseUrl => {
                self.reset_http_consent();
                &mut self.base_url
            }
            ApiSetupField::HttpConsent => &mut self.http_consent,
            ApiSetupField::Model => &mut self.model,
            ApiSetupField::Key => &mut self.value,
        }
    }

    fn visible_value(&self) -> String {
        match self.field {
            ApiSetupField::BaseUrl => self.base_url.clone(),
            ApiSetupField::HttpConsent => self.http_consent.clone(),
            ApiSetupField::Model => self.model.clone(),
            ApiSetupField::Key if self.value.is_empty() => String::new(),
            ApiSetupField::Key => "••••••••".into(),
        }
    }

    fn reset_http_consent(&mut self) {
        self.http_consent.clear();
        self.consented_endpoint = None;
    }

    fn return_to_url(&mut self) {
        self.reset_http_consent();
        self.field = ApiSetupField::BaseUrl;
    }

    fn validate_current(&mut self) -> Result<(), &'static str> {
        match self.field {
            ApiSetupField::BaseUrl => {
                self.reset_http_consent();
                self.base_url = validate_api_base_url(&self.base_url)?;
            }
            ApiSetupField::HttpConsent => {
                if self.http_consent != "TRUST" {
                    return Err("Type TRUST to acknowledge plaintext HTTP on this trusted LAN.");
                }
                self.consented_endpoint = Some(validate_api_base_url(&self.base_url)?);
            }
            ApiSetupField::Model => {
                self.model = self.model.trim().to_owned();
                if self.model.is_empty() || self.model.chars().any(char::is_control) {
                    return Err("Enter the model ID supplied by your API provider.");
                }
            }
            ApiSetupField::Key => {
                self.value = self.value.trim().to_owned();
                if self.value.is_empty() || self.value.chars().any(char::is_control) {
                    return Err("Enter a non-empty, single-line API key.");
                }
            }
        }
        Ok(())
    }
}

fn validate_api_base_url(value: &str) -> Result<String, &'static str> {
    let url = url::Url::parse(value.trim()).map_err(|_| "Enter a valid API Base URL.")?;
    if url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("Use a Base URL without credentials, query parameters, or a fragment.");
    }
    // Syntactic local-address classification only: never resolve DNS or probe the endpoint.
    let local = match url.host() {
        Some(url::Host::Domain(host)) => {
            let host = host.trim_end_matches('.');
            host == "localhost"
                || !host.contains('.')
                || [".local", ".lan", ".internal", ".home.arpa"]
                    .iter()
                    .any(|suffix| host.ends_with(suffix))
        }
        Some(url::Host::Ipv4(ip)) => ip.is_loopback() || ip.is_private() || ip.is_link_local(),
        Some(url::Host::Ipv6(ip)) => {
            ip.is_loopback() || ip.is_unique_local() || ip.is_unicast_link_local()
        }
        None => false,
    };
    if url.scheme() != "https" && !(url.scheme() == "http" && local) {
        return Err("Use HTTPS, or HTTP with an explicit local/LAN address or hostname.");
    }
    Ok(url.to_string().trim_end_matches('/').to_owned())
}

fn provider_edits(state: &ApiKeyInputState) -> Vec<codex_app_server_protocol::ConfigEdit> {
    vec![
        replace_config_value(
            "model_providers.ercode-api",
            serde_json::json!({
                "name": "ercode API",
                "base_url": state.base_url,
                "wire_api": "responses",
                "requires_openai_auth": true,
            }),
        ),
        replace_config_value("model_provider", serde_json::json!("ercode-api")),
        replace_config_value("model", serde_json::json!(state.model)),
        replace_config_value("features.api_key_model_discovery", serde_json::json!(false)),
    ]
}

// The server treats this batch as first-run-only: it rejects existing auth and
// suspends/drains remote catalog refreshes before applying the configuration.
async fn persist_api_setup<F, Fut>(
    state: &ApiKeyInputState,
    mut send: F,
) -> Result<(), &'static str>
where
    F: FnMut(ClientRequest) -> Fut,
    Fut: std::future::Future<Output = bool>,
{
    let endpoint = validate_api_base_url(&state.base_url)?;
    if endpoint.starts_with("http://")
        && state.consented_endpoint.as_deref() != Some(endpoint.as_str())
    {
        return Err("Confirm the plaintext HTTP warning for this endpoint before saving.");
    }
    if !send(ClientRequest::ConfigBatchWrite {
        request_id: onboarding_request_id(),
        params: ConfigBatchWriteParams {
            edits: provider_edits(state),
            file_path: None,
            expected_version: None,
            reload_user_config: true,
        },
    })
    .await
    {
        return Err(
            "API setup could not be saved. Sign out and unset authentication environment variables before changing endpoints, or check configuration permissions.",
        );
    }
    if !send(ClientRequest::LoginAccount {
        request_id: onboarding_request_id(),
        params: LoginAccountParams::ApiKey {
            api_key: state.value.clone(),
        },
    })
    .await
    {
        return Err(
            "Endpoint saved, but the API key could not be stored. Check your credential backend and retry.",
        );
    }
    Ok(())
}

pub(super) fn render_api_setup(
    area: Rect,
    buf: &mut Buffer,
    state: &ApiKeyInputState,
    error: Option<String>,
) {
    let (label, hint) = match state.field {
        ApiSetupField::BaseUrl => (
            "API Base URL · 1/3",
            "Include the API prefix, such as /v1. HTTPS or trusted LAN HTTP.",
        ),
        ApiSetupField::HttpConsent => (
            "HTTP plaintext warning · type TRUST to continue",
            "HTTP sends your API key and code in plaintext. Use only a trusted LAN.",
        ),
        ApiSetupField::Model => (
            "Model ID · 2/3",
            "Enter a model ID from your provider; no model lookup will run.",
        ),
        ApiSetupField::Key => (
            "API key · 3/3",
            "The key is masked. Only save if you trust this API endpoint.",
        ),
    };
    let mut lines: Vec<Line> = vec![
        "First-run API setup for ercode".bold().into(),
        "Already signed in? Sign out before changing your endpoint.".into(),
        "Responses API compatibility is required (not Chat Completions).".into(),
        hint.into(),
        "".into(),
        label.bold().into(),
        if state.visible_value().is_empty() {
            "Type or paste a value".dim().into()
        } else {
            state.visible_value().into()
        },
        "".into(),
    ];
    if state.field == ApiSetupField::HttpConsent {
        lines.push(format!("Confirm endpoint: {}", state.base_url).into());
        lines.push("Local hostnames are not verified; confirm the destination yourself.".into());
    }
    if state.field == ApiSetupField::Key {
        lines.push(format!("Endpoint: {}", state.base_url).into());
        lines.push(format!("Model: {}", state.model).into());
        lines.push(
            "Stored by your configured credential backend; it may use private auth.json."
                .dim()
                .into(),
        );
    }
    lines.push(if state.saving {
        "Saving locally…".into()
    } else {
        "Enter continue/save · Esc back · Ctrl+C quit".dim().into()
    });
    if let Some(error) = error {
        lines.push(error.red().into());
    }
    Paragraph::new(lines)
        .wrap(Wrap { trim: false })
        .render(area, buf);
}

impl AuthModeWidget {
    pub(super) fn render_api_key_configured(&self, area: Rect, buf: &mut Buffer) {
        Paragraph::new(vec![
            "✓ API configuration saved".fg(Color::Green).into(),
            "".into(),
            "  No connection test was made. Requests may incur provider charges.".into(),
        ])
        .wrap(Wrap { trim: false })
        .render(area, buf);
    }

    pub(super) fn render_api_key_entry(
        &self,
        area: Rect,
        buf: &mut Buffer,
        state: &ApiKeyInputState,
    ) {
        render_api_setup(area, buf, state, self.error_message());
    }

    pub(super) fn handle_api_key_entry_key_event(&mut self, event: &KeyEvent) -> bool {
        let mut save = None;
        {
            let mut guard = self.sign_in_state.write().unwrap();
            let SignInState::ApiKeyEntry(state) = &mut *guard else {
                return false;
            };
            if state.saving {
                return true;
            }
            if keys::CANCEL.is_pressed(*event) {
                match state.field {
                    ApiSetupField::BaseUrl => *guard = SignInState::PickMode,
                    ApiSetupField::HttpConsent | ApiSetupField::Model => state.return_to_url(),
                    ApiSetupField::Key => {
                        state.value.clear();
                        state.field = ApiSetupField::Model;
                    }
                }
                self.set_error(None);
            } else if keys::CONFIRM.is_pressed(*event) {
                match state.validate_current() {
                    Err(error) => self.set_error(Some(error.into())),
                    Ok(()) => {
                        self.set_error(None);
                        match state.field {
                            ApiSetupField::BaseUrl => {
                                state.field = if state.base_url.starts_with("http://") {
                                    ApiSetupField::HttpConsent
                                } else {
                                    ApiSetupField::Model
                                };
                            }
                            ApiSetupField::HttpConsent => state.field = ApiSetupField::Model,
                            ApiSetupField::Model => state.field = ApiSetupField::Key,
                            ApiSetupField::Key => {
                                state.saving = true;
                                save = Some(state.clone());
                            }
                        }
                    }
                }
            } else if event.kind == KeyEventKind::Press {
                match event.code {
                    KeyCode::Backspace => {
                        state.current_value_mut().pop();
                        self.set_error(None);
                    }
                    KeyCode::Char(c)
                        if !c.is_control()
                            && !event.modifiers.intersects(
                                KeyModifiers::SUPER | KeyModifiers::CONTROL | KeyModifiers::ALT,
                            ) =>
                    {
                        state.current_value_mut().push(c);
                        self.set_error(None);
                    }
                    _ => {}
                }
            }
        }
        if let Some(state) = save {
            self.save_api_setup(state);
        }
        self.request_frame.schedule_frame();
        true
    }

    pub(super) fn handle_api_key_entry_paste(&mut self, pasted: String) -> bool {
        let value = pasted.trim();
        if value.is_empty() || value.chars().any(char::is_control) {
            return false;
        }
        let mut guard = self.sign_in_state.write().unwrap();
        let SignInState::ApiKeyEntry(state) = &mut *guard else {
            return false;
        };
        if state.saving {
            return true;
        }
        // Pasting replaces a field, so pasting an endpoint does not append to its default.
        *state.current_value_mut() = value.to_owned();
        drop(guard);
        self.set_error(None);
        self.request_frame.schedule_frame();
        true
    }

    pub(super) fn start_api_key_entry(&mut self) {
        if !self.is_api_login_allowed() {
            self.disallow_api_login();
            return;
        }
        self.set_error(None);
        *self.sign_in_state.write().unwrap() =
            SignInState::ApiKeyEntry(ApiKeyInputState::default());
        self.request_frame.schedule_frame();
    }

    fn save_api_setup(&mut self, mut state: ApiKeyInputState) {
        if !self.is_api_login_allowed() {
            self.disallow_api_login();
            return;
        }
        let request_handle = self.app_server_request_handle.clone();
        let sign_in_state = self.sign_in_state.clone();
        let error = self.error.clone();
        let request_frame = self.request_frame.clone();
        tokio::spawn(async move {
            let message = persist_api_setup(&state, |request| {
                let handle = request_handle.clone();
                async move {
                    match request {
                        request @ ClientRequest::ConfigBatchWrite { .. } => handle
                            .request_typed::<serde_json::Value>(request)
                            .await
                            .is_ok(),
                        request @ ClientRequest::LoginAccount { .. } => matches!(
                            handle.request_typed::<LoginAccountResponse>(request).await,
                            Ok(LoginAccountResponse::ApiKey {})
                        ),
                        _ => false,
                    }
                }
            })
            .await
            .err();
            *error.write().unwrap() = message.map(str::to_owned);
            if message.is_none() {
                *sign_in_state.write().unwrap() = SignInState::ApiKeyConfigured;
            } else {
                state.saving = false;
                *sign_in_state.write().unwrap() = SignInState::ApiKeyEntry(state);
            }
            request_frame.schedule_frame();
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    #[test]
    fn api_setup_rejects_unsafe_endpoints_without_network() {
        for value in [
            "http://example.com/v1",
            "http://8.8.8.8/v1",
            "http://[2606:4700:4700::1111]/v1",
            "http://0.0.0.0/v1",
            "http://[::]/v1",
            "http://192.168.1.1.evil.example/v1",
            "https://user:key@example.com/v1",
            "https://example.com/v1?key=secret",
            "file:///tmp/api",
        ] {
            assert!(validate_api_base_url(value).is_err());
        }
        for value in [
            "https://example.com/v1",
            "http://127.0.0.1:8080/v1",
            "http://[::1]:8080/v1",
            "http://localhost:8080/v1",
            "http://192.168.1.4:8080/v1",
            "http://10.1.2.3/v1",
            "http://172.16.0.2/v1",
            "http://169.254.1.2/v1",
            "http://[fd00::1]:8080/v1",
            "http://[fe80::1]:8080/v1",
            "http://inference:8080/v1",
            "http://inference.local/v1",
            "http://inference.lan/v1",
            "http://inference.internal/v1",
            "http://inference.home.arpa/v1",
        ] {
            assert!(validate_api_base_url(value).is_ok());
        }
    }

    #[test]
    fn api_setup_never_displays_or_writes_key_in_config() {
        let state = ApiKeyInputState {
            base_url: "https://example.test/v1".into(),
            model: "example-model".into(),
            value: "fake-test-key-not-a-credential".into(),
            field: ApiSetupField::Key,
            saving: false,
            ..ApiKeyInputState::default()
        };
        let area = Rect::new(0, 0, 100, 16);
        let mut buffer = Buffer::empty(area);
        render_api_setup(area, &mut buffer, &state, None);
        let rendered = buffer
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(!rendered.contains(&state.value));
        assert!(rendered.contains("••••••••"));
        assert!(rendered.contains("https://example.test/v1"));
        assert_eq!(state.visible_value(), "••••••••");
        assert!(
            !serde_json::to_string(&provider_edits(&state))
                .unwrap()
                .contains(&state.value)
        );
    }
    #[tokio::test]
    async fn http_consent_is_endpoint_bound_and_required_before_any_rpc() {
        let mut state = ApiKeyInputState {
            base_url: "http://192.168.1.4:8080/v1/".into(),
            model: "test-model".into(),
            value: "synthetic-key-not-a-credential".into(),
            ..ApiKeyInputState::default()
        };
        state.validate_current().unwrap();
        state.field = ApiSetupField::HttpConsent;
        assert!(state.validate_current().is_err());
        let calls = std::cell::Cell::new(0);
        assert!(
            persist_api_setup(&state, |_| {
                calls.set(calls.get() + 1);
                std::future::ready(true)
            })
            .await
            .is_err()
        );
        assert_eq!(calls.get(), 0);
        state.http_consent = "TRUST".into();
        state.validate_current().unwrap();
        assert_eq!(
            state.consented_endpoint.as_deref(),
            Some("http://192.168.1.4:8080/v1")
        );
        assert!(
            persist_api_setup(&state, |_| {
                calls.set(calls.get() + 1);
                std::future::ready(true)
            })
            .await
            .is_ok()
        );
        assert_eq!(calls.get(), 2);
        // Even an out-of-band URL change cannot reuse consent for a different destination.
        state.base_url = "http://192.168.1.5:8080/v1".into();
        assert!(
            persist_api_setup(&state, |_| -> std::future::Ready<bool> {
                panic!("RPC without endpoint consent")
            })
            .await
            .is_err()
        );
        state.return_to_url();
        assert!(state.consented_endpoint.is_none());
        assert!(state.http_consent.is_empty());
        state.consented_endpoint = Some(state.base_url.clone());
        state.http_consent = "TRUST".into();
        // Both typing and paste use this same accessor and invalidate consent before mutation.
        *state.current_value_mut() = "http://inference.lan/v1".into();
        assert!(state.consented_endpoint.is_none());
        assert!(state.http_consent.is_empty());
    }

    #[tokio::test]
    async fn api_setup_spy_requires_config_success_before_key_storage() {
        let state = ApiKeyInputState {
            base_url: "https://example.test/v1".into(),
            model: "test-model".into(),
            value: "synthetic-key-for-local-test".into(),
            field: ApiSetupField::Key,
            saving: false,
            ..ApiKeyInputState::default()
        };
        for (config_ok, key_ok, expected_calls) in
            [(false, true, 1), (true, false, 2), (true, true, 2)]
        {
            let calls = std::cell::RefCell::new(Vec::new());
            let result =
                persist_api_setup(&state, |request| {
                    let accepted =
                        match &request {
                            ClientRequest::ConfigBatchWrite { params, .. } => {
                                assert!(
                                    !serde_json::to_string(params)
                                        .unwrap()
                                        .contains(&state.value)
                                );
                                assert!(params.edits.iter().any(|edit| edit.key_path
                                    == "features.api_key_model_discovery"
                                    && edit.value == serde_json::json!(false)));
                                config_ok
                            }
                            ClientRequest::LoginAccount {
                                params: LoginAccountParams::ApiKey { api_key },
                                ..
                            } => {
                                assert_eq!(api_key, &state.value);
                                assert_eq!(calls.borrow().len(), 1);
                                key_ok
                            }
                            _ => panic!("unexpected API setup operation"),
                        };
                    calls.borrow_mut().push(request);
                    std::future::ready(accepted)
                })
                .await;
            assert_eq!(result.is_ok(), config_ok && key_ok);
            assert_eq!(calls.borrow().len(), expected_calls);
            assert!(matches!(
                &calls.borrow()[0],
                ClientRequest::ConfigBatchWrite { .. }
            ));
        }
    }
}
