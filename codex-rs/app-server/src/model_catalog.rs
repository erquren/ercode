// Modified for ercode: serialize and suspend remote catalog access during local API setup.
//! Loads the model catalog for the current effective provider configuration.

use std::path::PathBuf;
use std::sync::Arc;

use codex_core::config::Config;
use codex_features::Feature;
use codex_login::AuthManager;
use codex_models_manager::manager::RefreshStrategy;
use codex_models_manager::manager::SharedModelsManager;
use codex_protocol::openai_models::ModelPreset;
use tokio::sync::Mutex;

use crate::config_manager::ConfigManager;
use crate::config_manager::StartupConfig;

/// Refreshes catalog routing without changing the routes retained by existing threads.
pub(crate) struct ModelCatalog {
    config_manager: ConfigManager,
    auth_manager: Arc<AuthManager>,
    startup_cwd: Option<PathBuf>,
    state: Mutex<CatalogState>,
    remote_refresh: RemoteRefreshGate,
}

#[derive(Default)]
struct RemoteRefreshGate {
    suspended: Mutex<bool>,
}

impl RemoteRefreshGate {
    async fn suspend(&self) {
        // All catalog fetches hold this lock until their request completes.
        *self.suspended.lock().await = true;
    }
}

struct CatalogState {
    needs_reload: bool,
    config: Arc<Config>,
    models_manager: SharedModelsManager,
}

impl ModelCatalog {
    pub(crate) fn new(
        config_manager: ConfigManager,
        startup_cwd: Option<PathBuf>,
        config: Arc<Config>,
        models_manager: SharedModelsManager,
        auth_manager: Arc<AuthManager>,
    ) -> Self {
        Self {
            config_manager,
            auth_manager,
            startup_cwd,
            remote_refresh: RemoteRefreshGate::default(),
            state: Mutex::new(CatalogState {
                needs_reload: false,
                config,
                models_manager,
            }),
        }
    }

    /// Local API setup must drain old-route requests before config or credentials change.
    /// Keep remote discovery suspended for this process; onboarding also persists it disabled.
    pub(crate) async fn suspend_remote_refresh_for_api_setup(&self) {
        self.remote_refresh.suspend().await;
    }

    /// Marks catalog configuration stale after an account transition.
    pub(crate) async fn invalidate(&self) {
        self.state.lock().await.needs_reload = true;
    }

    pub(crate) async fn set_api_key_model_discovery_enabled(&self, enabled: bool) {
        self.state
            .lock()
            .await
            .models_manager
            .set_api_key_model_discovery_enabled(enabled);
    }

    #[expect(
        clippy::await_holding_invalid_type,
        reason = "catalog reloads are serialized with account invalidation; model fetching holds no lock"
    )]
    pub(crate) async fn current(&self) -> std::io::Result<(Arc<Config>, SharedModelsManager)> {
        let mut state = self.state.lock().await;
        if state.needs_reload {
            // Keep the large config-loading future off callers' stacks.
            let config = Box::pin(
                self.config_manager
                    .load_startup_config(self.startup_cwd.clone()),
            )
            .await?;
            let StartupConfig {
                config,
                used_defaults,
            } = config;
            if config.model_provider_id != state.config.model_provider_id
                || config.model_provider != state.config.model_provider
                || config.model_catalog != state.config.model_catalog
            {
                state.models_manager =
                    codex_core::build_models_manager(&config, Arc::clone(&self.auth_manager));
            }
            state.models_manager.set_api_key_model_discovery_enabled(
                config.features.enabled(Feature::ApiKeyModelDiscovery),
            );
            state.config = Arc::new(config);
            // Try again on the next access so a temporary fallback cannot pin the provider.
            state.needs_reload = used_defaults;
        }
        Ok((Arc::clone(&state.config), Arc::clone(&state.models_manager)))
    }

    pub(crate) async fn list_models(
        &self,
        refresh_strategy: RefreshStrategy,
    ) -> std::io::Result<Vec<ModelPreset>> {
        let (config, models_manager) = self.current().await?;
        self.list_models_from_snapshot(config, models_manager, refresh_strategy)
            .await
    }

    pub(crate) async fn list_models_from_snapshot(
        &self,
        config: Arc<Config>,
        models_manager: SharedModelsManager,
        refresh_strategy: RefreshStrategy,
    ) -> std::io::Result<Vec<ModelPreset>> {
        let remote_refresh = self.remote_refresh.suspended.lock().await;
        let refresh_strategy = if *remote_refresh {
            RefreshStrategy::Offline
        } else {
            refresh_strategy
        };
        // Retain the guard through the awaited fetch so suspension drains in-flight work.
        // Keep the existing policy check even for a warm catalog. A policy
        // edit alone must not make the retained provider silently permissible.
        self.config_manager
            .check_thread_model_provider(&config)
            .await?;
        Ok(models_manager
            .list_models(refresh_strategy, config.http_client_factory())
            .await)
    }
}

#[cfg(test)]
mod ercode_tests {
    use super::RemoteRefreshGate;
    use std::sync::Arc;
    use std::time::Duration;

    #[tokio::test]
    async fn api_setup_gate_drains_inflight_requests_before_suspending() {
        let gate = Arc::new(RemoteRefreshGate::default());
        let active_request = gate.suspended.lock().await;
        let pending_gate = Arc::clone(&gate);
        let mut suspension = tokio::spawn(async move { pending_gate.suspend().await });
        assert!(
            tokio::time::timeout(Duration::from_millis(10), &mut suspension)
                .await
                .is_err()
        );
        drop(active_request);
        suspension.await.unwrap();
        assert!(*gate.suspended.lock().await);
    }
}
