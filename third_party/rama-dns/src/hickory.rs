//! dns using the [`hickory_resolver`] crate

use std::{
    net::{Ipv4Addr, Ipv6Addr},
    sync::Arc,
};

pub use hickory_resolver as resolver;
use hickory_resolver::{
    TokioResolver,
    config::ResolverConfig,
    net::NetError,
    net::runtime::TokioRuntimeProvider,
    proto::rr::{Name, RData},
};

use rama_core::error::{ErrorContext, OpaqueError};
use rama_core::telemetry::tracing;
use rama_net::address::Domain;
use rama_utils::macros::generate_set_and_with;

use crate::DnsResolver;

#[derive(Debug, Clone)]
/// [`DnsResolver`] using the [`hickory_resolver`] crate
pub struct HickoryDns(Arc<Result<TokioResolver, NetError>>);

impl Default for HickoryDns {
    #[cfg(any(target_family = "unix", target_os = "windows"))]
    fn default() -> Self {
        Self::try_new_system().unwrap_or_else(|err| {
            tracing::warn!(
                "fail to create system HickoryDns client: fallback to cloudflare: {err}"
            );
            Self::new_cloudflare()
        })
    }

    #[cfg(not(any(target_family = "unix", target_os = "windows")))]
    fn default() -> Self {
        Self::new_cloudflare()
    }
}

impl HickoryDns {
    #[inline]
    /// Construct a [`HickoryDnsBuilder`] used to build
    /// a custom [`HickoryDns`] instead of the default [`HickoryDns::new`].
    #[must_use]
    pub fn builder() -> HickoryDnsBuilder {
        HickoryDnsBuilder::default()
    }

    #[inline]
    /// Construct a new [`HickoryDns`] instance with the [`Default`] setup.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[inline]
    /// Construct a new non-shared [`HickoryDns`] instance using Google's nameservers.
    ///
    /// Creates a default configuration, using `8.8.8.8`, `8.8.4.4` and `2001:4860:4860::8888`,
    /// `2001:4860:4860::8844` (thank you, Google).
    ///
    /// Please see Google's [privacy
    /// statement](https://developers.google.com/speed/public-dns/privacy) for important information
    /// about what they track, many ISP's track similar information in DNS.
    ///
    /// To use the system configuration see: [`Self::new_system`].
    pub fn new_google() -> Self {
        tracing::trace!("create HickoryDns resolver using default google config");
        Self::builder()
            .with_config(ResolverConfig::udp_and_tcp(&self::resolver::config::GOOGLE))
            .build()
    }

    #[inline]
    /// Construct a new non-shared [`HickoryDns`] instance using Cloudflare's nameservers.
    ///
    /// Creates a default configuration, using `1.1.1.1`, `1.0.0.1` and `2606:4700:4700::1111`, `2606:4700:4700::1001` (thank you, Cloudflare).
    ///
    /// Please see: <https://www.cloudflare.com/dns/>
    ///
    /// To use the system configuration see: [`Self::new_system`].
    pub fn new_cloudflare() -> Self {
        tracing::trace!("create HickoryDns resolver using default cloudflare config");
        Self::builder()
            .with_config(ResolverConfig::udp_and_tcp(
                &self::resolver::config::CLOUDFLARE,
            ))
            .build()
    }

    #[inline]
    /// Construct a new non-shared [`HickoryDns`] instance using Quad9's nameservers.
    ///
    /// Creates a configuration, using `9.9.9.9`, `149.112.112.112` and `2620:fe::fe`, `2620:fe::fe:9`,
    /// the "secure" variants of the quad9 settings (thank you, Quad9).
    ///
    /// Please see: <https://www.quad9.net/faq/>
    ///
    /// To use the system configuration see: [`Self::new_system`].
    pub fn new_quad9() -> Self {
        tracing::trace!("create HickoryDns resolver using default quad9 config");
        Self::builder()
            .with_config(ResolverConfig::udp_and_tcp(&self::resolver::config::QUAD9))
            .build()
    }

    #[cfg(any(target_family = "unix", target_os = "windows"))]
    /// Construct a new [`HickoryDns`] with the system configuration.
    ///
    /// This will use `/etc/resolv.conf` on Unix OSes and the registry on Windows.
    pub fn try_new_system() -> Result<Self, OpaqueError> {
        tracing::trace!("try to create HickoryDns resolver using system config");
        Ok(TokioResolver::builder_tokio()
            .context("build async dns resolver with system conf")
            .inspect_err(|err| {
                tracing::debug!("failed to create HickoryDns resolver using system config: {err:?}")
            })?
            .build()
            .context("initialize async dns resolver")?
            .into())
    }
}

impl From<TokioResolver> for HickoryDns {
    fn from(value: TokioResolver) -> Self {
        Self(Arc::new(Ok(value)))
    }
}

#[derive(Debug, Clone, Default)]
/// A [`Builder`] to [`build`][`Self::build`] a [`HickoryDns`] instance.
pub struct HickoryDnsBuilder {
    config: Option<self::resolver::config::ResolverConfig>,
    options: Option<self::resolver::config::ResolverOpts>,
}

impl HickoryDnsBuilder {
    generate_set_and_with! {
        /// Define the [`ResolverConfig`][`config::ResolverConfig`] used.
        pub fn config(mut self, config: Option<self::resolver::config::ResolverConfig>) -> Self {
            self.config = config;
            self
        }
    }

    generate_set_and_with! {
        /// Define the [`ResolverOpts`][`config::ResolverOpts`] used.
        #[must_use]
        pub fn options(mut self, options: Option<self::resolver::config::ResolverOpts>) -> Self {
            self.options = options;
            self
        }
    }

    /// Build a [`HickoryDns`] instance, consuming [`self`].
    ///
    /// [`Clone`] the [`HickoryDnsBuilder`] prior to calling this method in case you
    /// still need the builder afterwards.
    pub fn build(self) -> HickoryDns {
        let mut resolver_builder = TokioResolver::builder_with_config(
            self.config.unwrap_or_else(|| {
                ResolverConfig::udp_and_tcp(&self::resolver::config::CLOUDFLARE)
            }),
            TokioRuntimeProvider::default(),
        );
        if let Some(options) = self.options {
            *resolver_builder.options_mut() = options;
        }
        HickoryDns(Arc::new(resolver_builder.build()))
    }
}

impl HickoryDns {
    fn resolver(&self) -> Result<&TokioResolver, OpaqueError> {
        // Preserve the infallible Rama builder API, but fail lookups closed if
        // Hickory rejects initialization. Never panic or silently change DNS routes.
        self.0
            .as_ref()
            .as_ref()
            .map_err(|error| OpaqueError::from_display(error.to_string()))
    }
}

impl DnsResolver for HickoryDns {
    type Error = OpaqueError;

    async fn txt_lookup(&self, domain: Domain) -> Result<Vec<Vec<u8>>, Self::Error> {
        let name = fqdn_from_domain(domain)?;

        let mut results = vec![];
        for txt in self
            .resolver()?
            .txt_lookup(name)
            .await
            .context("lookup TXT entry")?
            .answers()
            .iter()
        {
            if let RData::TXT(txt) = &txt.data {
                for value in txt.txt_data.iter() {
                    results.push(value.to_vec());
                }
            }
        }
        Ok(results)
    }

    async fn ipv4_lookup(&self, domain: Domain) -> Result<Vec<Ipv4Addr>, Self::Error> {
        let name = fqdn_from_domain(domain)?;
        Ok(self
            .resolver()?
            .ipv4_lookup(name)
            .await
            .context("lookup IPv4 address(es)")?
            .answers()
            .iter()
            .filter_map(|record| match &record.data {
                RData::A(ip) => Some(ip.0),
                _ => None,
            })
            .collect())
    }

    async fn ipv6_lookup(&self, domain: Domain) -> Result<Vec<Ipv6Addr>, Self::Error> {
        let name = fqdn_from_domain(domain)?;
        Ok(self
            .resolver()?
            .ipv6_lookup(name)
            .await
            .context("lookup IPv6 address(es)")?
            .answers()
            .iter()
            .filter_map(|record| match &record.data {
                RData::AAAA(ip) => Some(ip.0),
                _ => None,
            })
            .collect())
    }
}

fn fqdn_from_domain(domain: Domain) -> Result<Name, OpaqueError> {
    let mut name = Name::from_utf8(domain).context("try to consume a Domain as a Dns Name")?;
    name.set_fqdn(true);
    Ok(name)
}

#[cfg(test)]
mod compatibility_tests {
    use super::*;

    #[tokio::test]
    async fn preserves_infallible_builder_and_localhost_resolution() {
        let resolver = HickoryDns::builder().build();
        assert!(resolver.resolver().is_ok());
        // RFC 6761 localhost handling does not require contacting a DNS server.
        let addresses = resolver
            .ipv4_lookup("localhost".parse::<Domain>().unwrap())
            .await
            .unwrap();
        assert!(addresses.contains(&Ipv4Addr::LOCALHOST));
    }

    #[tokio::test]
    async fn initialization_failure_is_returned_without_network_fallback() {
        let resolver = HickoryDns(Arc::new(Err(NetError::from(
            "fixture initialization failure",
        ))));
        for kind in ["ipv4", "ipv6", "txt"] {
            let domain = "example.com".parse::<Domain>().unwrap();
            let error = match kind {
                "ipv4" => resolver.ipv4_lookup(domain).await.unwrap_err(),
                "ipv6" => resolver.ipv6_lookup(domain).await.unwrap_err(),
                _ => resolver.txt_lookup(domain).await.unwrap_err(),
            };
            assert!(error.to_string().contains("fixture initialization failure"));
        }
    }

    #[test]
    fn domain_queries_remain_fully_qualified() {
        let name = fqdn_from_domain("example.com".parse::<Domain>().unwrap()).unwrap();
        assert!(name.is_fqdn());
        assert_eq!(name.to_utf8(), "example.com.");
    }
}
