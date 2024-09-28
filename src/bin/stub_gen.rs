use pyo3_stub_gen::Result;
use ranked_choice_vote;

fn main() -> Result<()> {
    // `stub_info` is a function defined by `define_stub_info_gatherer!` macro.
    let stub = ranked_choice_vote::stub_info()?;
    stub.generate()?;
    Ok(())
}