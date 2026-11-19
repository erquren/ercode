# Rama DNS compatibility patch

Source: `rama-dns` 0.3.0-alpha.4 from crates.io, corresponding to
https://github.com/plabayo/rama/tree/rama-0.3.0-alpha.4/rama-dns .
Original crate checksum: e340fef2799277e204260b17af01bc23604712092eacd6defe40167f304baed8.
License: MIT OR Apache-2.0 (upstream license files included).

The original Rama APIs are retained to avoid upgrading the entire proxy stack.
The Hickory dependency is upgraded to 0.26.3 (including hickory-net security
fixes), and its moved runtime/Name imports and fallible builder are adapted.
Because the existing public Rama builder is infallible, an initialization error
is retained and returned from lookups rather than panicking or choosing another
DNS route. The existing fallible system constructor propagates build errors.

No vulnerability/advisory suppressions are added. Remove this patch after the
proxy stack migrates to a maintained Rama release with current Hickory support.
