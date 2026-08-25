// Is libm::log the source of the breaker-path divergence? the determinism notes's
// sweep put libm ahead of std on log, but "ahead" is not "exact".
fn main() {
    let mut libm_vs_std = 0u32;
    let mut worst = 0i64;
    let n = 200_000;
    for i in 1..=n {
        // Ratios in the band the breaker re-derivation actually produces:
        // newPrice/fv where newPrice is clamped to +/-25% of previousClose.
        let x = 0.5 + 1.5 * (i as f64 / n as f64);
        let l = libm::log(x);
        let s = x.ln();
        if l.to_bits() != s.to_bits() {
            libm_vs_std += 1;
            let d = (l.to_bits() as i64 - s.to_bits() as i64).abs();
            if d > worst {
                worst = d;
            }
        }
    }
    println!("libm::log vs std ln over [0.5, 2.0]: {libm_vs_std} of {n} differ, worst {worst} ULP");
}
