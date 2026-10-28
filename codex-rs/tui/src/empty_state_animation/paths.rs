//! Ercode terminal glyph and the custom mark traced from the supplied image.
// The custom mark is rasterized as terminal Braille by the shared animation renderer.
// Internal constant names are retained to minimize changes to the renderer.

pub(super) const CODEX: (&str, [f64; 4]) = (
    "M2 3L9 10L2 17L4 19L13 10L4 1ZM11 16H19V19H11Z",
    [2.0, 1.0, 19.0, 19.0],
);
// Trace only the black silhouette; the reference image's white margin is omitted.
pub(super) const OPENAI: (&str, [f64; 4]) = (
    "M66 71H392V100H162V192H293V161H220V132H321V313H392V342H321V403H293V342H66V313H134V253H239V282H162V313H293V221H134V100H66Z",
    [66.0, 71.0, 392.0, 403.0],
);
