// How often does the 1-ULP volume difference survive Math.floor?
// The exception is only benign if the floor absorbs it almost always.
use pretium::market::{intraday_volume, MarketStatus};

fn main() {
    // A representative avgVolume/390 base, scaled by the curve as the tick does.
    let mut crossings = 0u32;
    let mut checked = 0u32;
    for step in 0..=390 {
        let t = step as f64 / 390.0;
        let curve = intraday_volume(t, MarketStatus::Open);
        // The neighbouring double, standing in for what V8 might produce.
        let neighbour = f64::from_bits(curve.to_bits() + 1);
        for base in [1_000_000.0f64 / 390.0, 5_000.0, 250_000.0, 87_654.0] {
            for mult in [1.0f64, 2.5, 9.0] {
                let a = (curve * base * mult).floor();
                let b = (neighbour * base * mult).floor();
                checked += 1;
                if a != b {
                    crossings += 1;
                }
            }
        }
    }
    println!(
        "{crossings} of {checked} volume computations ({:.4}%) floor differently \
              when the curve moves by one ULP",
        100.0 * crossings as f64 / checked as f64
    );
}
