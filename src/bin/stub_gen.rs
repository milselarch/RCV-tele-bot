use pyo3_stub_gen::Result;
use py_rcv;

fn main() -> Result<()> {
    // `stub_info` is a function defined by `define_stub_info_gatherer!` macro.
    let stub = py_rcv::rcv_interface::stub_info()?;
    stub.generate()?;
    Ok(())
}