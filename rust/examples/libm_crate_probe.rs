// Does the pure-Rust `libm` crate (a MUSL/fdlibm port) match V8, where the
// platform libm does not? V8's Math.cos is itself an fdlibm derivative, so
// there is a real chance these agree exactly.
fn b(x: f64) -> String {
    format!("{:016x}", x.to_bits())
}
fn main() {
    let v = f64::from_bits(0x3fcaeda146800000);
    let arg = 2.0 * std::f64::consts::PI * v;
    println!("arg              = {}", b(arg));
    println!("std   cos        = {}", b(arg.cos()));
    println!("libm  cos        = {}", b(libm::cos(arg)));
    println!("V8    cos        = 3fcf89e359114799   <-- target");
    println!();
    println!("std   sin        = {}", b(arg.sin()));
    println!("libm  sin        = {}", b(libm::sin(arg)));
    println!("V8    sin        = 3fef036f77591ae7   <-- target");
    println!();
    let u = f64::from_bits(0x3fe2a91537a00000);
    println!("std   ln         = {}", b(u.ln()));
    println!("libm  log        = {}", b(libm::log(u)));
    println!("V8    log        = bfe1422cc2c359e4   <-- target");
}
