// Does a runtime-computed phi reproduce V8's? The goldens say "may differ"
// and hardcoding is the safe move either way, but an unmeasured "may" is
// how a one-ULP compounding bug gets shrugged off.
fn main() {
    let v8 = f64::from_bits(0x3FEF_A1E8_27A1_B38C);
    let exponent = 1.0f64 / 60.0;
    let std_pow = 0.5f64.powf(exponent);
    let libm_pow = libm::pow(0.5, exponent);

    // The exponent itself is inexact; confirm we feed pow the same double.
    println!(
        "exponent bits  {:016X} (V8 records 3F91111111111111)",
        exponent.to_bits()
    );
    println!("V8   {:016X}  {}", v8.to_bits(), v8);
    println!(
        "std  {:016X}  {}  ulp_diff={}",
        std_pow.to_bits(),
        std_pow,
        (std_pow.to_bits() as i64 - v8.to_bits() as i64).abs()
    );
    println!(
        "libm {:016X}  {}  ulp_diff={}",
        libm_pow.to_bits(),
        libm_pow,
        (libm_pow.to_bits() as i64 - v8.to_bits() as i64).abs()
    );
}
