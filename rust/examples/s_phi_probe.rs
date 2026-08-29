// S_PHI_TICK compounds 390x per simulated day. A one-ULP difference here is
// not a rounding curiosity — it is a different decay rate applied all day.
use tradefloor::mispricing::MISPRICING_PHI;
fn main() {
    let v8 = f64::from_bits(0x3FFF_FFFF_45C6_6FB5); // placeholder, replaced below
    let exp = 1.0f64 / 390.0;
    println!("exponent      {:016X}", exp.to_bits());
    println!(
        "libm::pow     {:016X}  {}",
        libm::pow(MISPRICING_PHI, exp).to_bits(),
        libm::pow(MISPRICING_PHI, exp)
    );
    println!(
        "std powf      {:016X}  {}",
        MISPRICING_PHI.powf(exp).to_bits(),
        MISPRICING_PHI.powf(exp)
    );
    let _ = v8;
}
