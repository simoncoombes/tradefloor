// Compares Rust's libm against V8's on the exact inputs where next_normal
// diverged. Run: cargo run --example libm_probe
fn b(x: f64) -> String {
    format!("{:016x}", x.to_bits())
}
fn main() {
    let u = f64::from_bits(0x3fe2a91537a00000);
    let v = f64::from_bits(0x3fcaeda146800000);
    println!("u                = {}", b(u));
    println!("v                = {}", b(v));
    println!("log(u)           = {}", b(u.ln()));
    println!("-2*log(u)        = {}", b(-2.0 * u.ln()));
    println!("sqrt(-2*log(u))  = {}", b((-2.0 * u.ln()).sqrt()));
    println!("2*PI*v           = {}", b(2.0 * std::f64::consts::PI * v));
    println!(
        "cos(2*PI*v)      = {}",
        b((2.0 * std::f64::consts::PI * v).cos())
    );
    println!(
        "sin(2*PI*v)      = {}",
        b((2.0 * std::f64::consts::PI * v).sin())
    );
    println!(
        "final            = {}",
        b((-2.0 * u.ln()).sqrt() * (2.0 * std::f64::consts::PI * v).cos())
    );
}
