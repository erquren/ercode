//! Original ercode terminal and E glyphs for the existing logo animation.
// Modified for ercode: replace upstream product marks with original simple glyphs.
// Internal constant names are retained to minimize changes to the renderer.

pub(super) const CODEX: (&str, [f64; 4]) = (
    "M2 3L9 10L2 17L4 19L13 10L4 1ZM11 16H19V19H11Z",
    [2.0, 1.0, 19.0, 19.0],
);
pub(super) const OPENAI: (&str, [f64; 4]) =
    ("M3 2H17V5H7V8H15V11H7V15H17V18H3Z", [3.0, 2.0, 17.0, 18.0]);
