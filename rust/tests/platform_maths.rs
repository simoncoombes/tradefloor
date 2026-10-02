//! No library code calls the platform's maths library, or a float `max`/`min`.
//!
//! The crate promises the same market on every platform, and it keeps that
//! promise by doing its transcendental maths in `mathx`, in Rust, rather than
//! through `f64::ln`, `f64::exp` and friends, which call whatever `libm` the
//! target links. Two of those can differ in the last bit, and a last-bit
//! difference compounds over a simulated year into a different market.
//! `f64::max` and `f64::min` are refused on the same footing for a different
//! reason: they treat NaN as missing and return the other operand, so a
//! poisoned input comes out as a plausible number. `mathx::max` and
//! `mathx::min` propagate it.
//!
//! This guard used to live in `tests/mathx_parity.rs`, which reads the parity
//! corpus and so does not ship in the package. It also stopped reading a file
//! at the file's first `#[cfg(test)]` line and treated everything after it as
//! test code. `market/factor_vol.rs` has a `#[cfg(test)] use` at line 119 of
//! 1,871, so the guard never saw `alpha_beta_at`, which called `f64::ln` in
//! 0.8.5. No shipped preset reaches that line (every one sets
//! `market_vol_alpha_excursion` to 0.0, and the function returns before the
//! log), so no known-answer digest could see it either; a user who set the
//! dial with `ModelParams::with_override` ran the platform log.
//!
//! So this walks every `.rs` file under `src/` except `mathx.rs`, drops
//! comments, string and char literals, and each `#[cfg(test)]` item on its
//! own (a module, a function, a `use`), and fails on any remaining call.
//! `sqrt` is allowed: IEEE 754 requires it to be correctly rounded, so every
//! platform gives the same bits. `mul_add` is refused because it is a fused
//! instruction on one target and a software emulation on another.

use std::fs;
use std::path::{Path, PathBuf};

/// Float methods whose result depends on the platform's maths library, or,
/// for `mul_add` and `powi`, on the target's instructions.
const PLATFORM_METHODS: &[&str] = &[
    "ln", "log", "log2", "log10", "ln_1p", "exp", "exp2", "exp_m1", "powf", "powi", "sin",
    "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh", "asinh", "acosh",
    "atanh", "cbrt", "hypot", "sin_cos", "mul_add", "max", "min",
];

/// Integer `max`/`min` calls the text match cannot tell from float ones.
/// Only floats swallow NaN, so these are allowed by spelling.
const INTEGER_MIN_MAX: &[&str] = &["levels.max(", "len().max("];

fn rust_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let mut entries: Vec<_> = fs::read_dir(dir)
        .unwrap_or_else(|e| panic!("cannot read {}: {e}", dir.display()))
        .map(|e| e.unwrap().path())
        .collect();
    entries.sort();
    for path in entries {
        if path.is_dir() {
            rust_files(&path, out);
        } else if path.extension().is_some_and(|x| x == "rs") {
            out.push(path);
        }
    }
}

/// The source with comments and the contents of string and char literals
/// replaced by spaces, so line numbers survive and neither a doc comment that
/// writes `x.ln()` nor a brace inside a string can mislead the scan below.
fn blank_comments_and_literals(src: &str) -> String {
    let b = src.as_bytes();
    let mut out = b.to_vec();
    let blank = |out: &mut Vec<u8>, from: usize, to: usize| {
        for c in &mut out[from..to] {
            if *c != b'\n' {
                *c = b' ';
            }
        }
    };
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'/' if b.get(i + 1) == Some(&b'/') => {
                let end = src[i..].find('\n').map_or(b.len(), |n| i + n);
                blank(&mut out, i, end);
                i = end;
            }
            b'/' if b.get(i + 1) == Some(&b'*') => {
                let (mut depth, mut j) = (1, i + 2);
                while j < b.len() && depth > 0 {
                    if b[j] == b'/' && b.get(j + 1) == Some(&b'*') {
                        depth += 1;
                        j += 2;
                    } else if b[j] == b'*' && b.get(j + 1) == Some(&b'/') {
                        depth -= 1;
                        j += 2;
                    } else {
                        j += 1;
                    }
                }
                blank(&mut out, i, j);
                i = j;
            }
            b'r' if matches!(b.get(i + 1), Some(b'"') | Some(b'#'))
                && (i == 0 || !(b[i - 1].is_ascii_alphanumeric() || b[i - 1] == b'_')) =>
            {
                let mut hashes = 0;
                let mut j = i + 1;
                while b.get(j) == Some(&b'#') {
                    hashes += 1;
                    j += 1;
                }
                if b.get(j) != Some(&b'"') {
                    i += 1;
                    continue;
                }
                let close = format!("\"{}", "#".repeat(hashes));
                let end = src[j + 1..]
                    .find(&close)
                    .map_or(b.len(), |n| j + 1 + n + close.len());
                blank(&mut out, j + 1, end.saturating_sub(close.len()));
                i = end;
            }
            b'"' => {
                let mut j = i + 1;
                while j < b.len() && b[j] != b'"' {
                    j += if b[j] == b'\\' { 2 } else { 1 };
                }
                blank(&mut out, i + 1, j.min(b.len()));
                i = j + 1;
            }
            b'\'' => {
                // A char literal is 'x' or '\..'; anything else is a lifetime.
                if b.get(i + 1) == Some(&b'\\') {
                    let end = src[i + 3..].find('\'').map_or(b.len(), |n| i + 3 + n);
                    blank(&mut out, i + 1, end);
                    i = end + 1;
                } else if let Some(c) = src[i + 1..].chars().next() {
                    let after = i + 1 + c.len_utf8();
                    if b.get(after) == Some(&b'\'') {
                        blank(&mut out, i + 1, after);
                        i = after + 1;
                    } else {
                        i += 1;
                    }
                } else {
                    i += 1;
                }
            }
            _ => i += 1,
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Blank every item carrying `#[cfg(test)]`: a test module, a test-only
/// function, a test-only `use`. Runs on text already cleared of comments and
/// literals, so braces balance.
fn blank_test_items(src: &str) -> String {
    let mut out = src.as_bytes().to_vec();
    let mut from = 0;
    while let Some(n) = src[from..].find("#[cfg(test)]") {
        let start = from + n;
        let b = src.as_bytes();
        let mut j = start + "#[cfg(test)]".len();
        let mut depth = 0usize;
        while j < b.len() {
            match b[j] {
                b'{' => depth += 1,
                b'}' => {
                    depth -= 1;
                    if depth == 0 {
                        j += 1;
                        break;
                    }
                }
                b';' if depth == 0 => {
                    j += 1;
                    break;
                }
                _ => {}
            }
            j += 1;
        }
        for c in &mut out[start..j.min(b.len())] {
            if *c != b'\n' {
                *c = b' ';
            }
        }
        from = j.min(b.len());
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `(line, text)` for every call to a platform maths method in `code`.
fn platform_calls(code: &str) -> Vec<(usize, String)> {
    let mut found = Vec::new();
    for (n, line) in code.lines().enumerate() {
        for method in PLATFORM_METHODS {
            for spelling in [format!(".{method}("), format!("f64::{method}(")] {
                let mut at = 0;
                while let Some(k) = line[at..].find(&spelling) {
                    let open = at + k + spelling.len();
                    at = open;
                    // `rng.log()` is the draw log and `iter.max()` an
                    // iterator's; `f64::log`, `f64::max` and `f64::min` always
                    // take an argument, so an empty argument list is not one.
                    let rest = line[open..].trim_start();
                    if matches!(*method, "log" | "max" | "min") && rest.starts_with(')') {
                        continue;
                    }
                    if INTEGER_MIN_MAX.iter().any(|ok| line[..open].ends_with(ok)) {
                        continue;
                    }
                    found.push((n + 1, line.trim().to_string()));
                }
            }
        }
    }
    found.sort();
    found.dedup();
    found
}

#[test]
fn no_std_transcendentals_outside_mathx() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files = Vec::new();
    rust_files(&src, &mut files);
    assert!(files.len() > 20, "found only {} source files under {}", files.len(), src.display());

    let mut offences = Vec::new();
    for path in &files {
        if path.file_name().is_some_and(|f| f == "mathx.rs") {
            continue;
        }
        let text = fs::read_to_string(path).unwrap();
        let code = blank_test_items(&blank_comments_and_literals(&text));
        for (line, snippet) in platform_calls(&code) {
            let rel = path.strip_prefix(&src).unwrap().display();
            offences.push(format!("src/{rel}:{line}: {snippet}"));
        }
    }
    assert!(
        offences.is_empty(),
        "library code calls the platform's maths library or a NaN-swallowing max/min; \
         use the crate::mathx equivalent:\n  {}",
        offences.join("\n  ")
    );
}

#[test]
fn the_scan_sees_a_platform_call_and_ignores_comments_strings_and_tests() {
    let sample = r##"
fn live(x: f64) -> f64 { x.ln() + f64::exp(x) }
// x.sin() in a comment
/* x.cos() in a block */
fn text() -> &'static str { "y.tan()" }
fn raw() -> &'static str { r#"z.powf(2.0)"# }
fn draw_log(rng: &Rng) { let _ = rng.log(); }
fn brace() -> char { '{' }
fn quote() -> char { '\'' }
fn life<'a>(x: &'a f64) -> f64 { x.exp() }
fn sqrt_is_exact(x: f64) -> f64 { x.sqrt() }
#[cfg(test)]
mod tests {
    fn t(x: f64) -> f64 { x.atan2(1.0) }
}
fn after(x: f64) -> f64 { x.mul_add(2.0, 1.0) }
fn floats(a: f64) -> f64 { a.max(0.0) + f64::min(a, 1.0) }
fn ints(levels: usize, v: &[f64]) -> usize { levels.max(1) + v.len().max(1) }
fn iters(v: &[u32]) -> Option<u32> { v.iter().copied().max() }
"##;
    let code = blank_test_items(&blank_comments_and_literals(sample));
    let lines: Vec<usize> = platform_calls(&code).into_iter().map(|(n, _)| n).collect();
    assert_eq!(lines, vec![2, 10, 16, 17], "{code}");
}
