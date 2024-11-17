use pyo3::prelude::*;

use crate::rcv_interface::VotesCounter;
pub mod rcv_interface;

#[pymodule]
fn py_rcv(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<VotesCounter>()?;
    Ok(())
}
