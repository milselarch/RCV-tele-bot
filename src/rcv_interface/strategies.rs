use crate::pymethods;
use enum_iterator::{all, Sequence};
use pyo3::{pyclass, Bound, PyResult};
use pyo3::exceptions::PyValueError;
use pyo3_stub_gen::define_stub_info_gatherer;
use pyo3_stub_gen::derive::{gen_stub_pyclass_enum, gen_stub_pymethods};
use trie_rcv::EliminationStrategies;

#[gen_stub_pyclass_enum]
#[pyclass(eq, eq_int)]
#[derive(Copy, Clone, PartialEq, Sequence)]
#[repr(u8)]
pub enum PyEliminationStrategies {
    DowdallScoring = 0,
    EliminateAll = 1,
    RankedPairs = 2,
    CondorcetRankedPairs = 3
}
impl PyEliminationStrategies {
    fn _from_num(value: u8) -> Option<PyEliminationStrategies> {
        for strategy in all::<PyEliminationStrategies>() {
            if strategy as u8 == value {
                return Some(strategy);
            }
        }
        None
    }
    pub fn _to_strategy(&self) -> EliminationStrategies {
        match self {
            PyEliminationStrategies::DowdallScoring =>
                EliminationStrategies::DowdallScoring,
            PyEliminationStrategies::EliminateAll =>
                EliminationStrategies::EliminateAll,
            PyEliminationStrategies::RankedPairs =>
                EliminationStrategies::RankedPairs,
            PyEliminationStrategies::CondorcetRankedPairs =>
                EliminationStrategies::CondorcetRankedPairs
        }
    }
}
#[gen_stub_pymethods]
#[pymethods]
impl PyEliminationStrategies {
    #[new]
    pub fn new(value: u8) -> PyResult<Self> {
        let converted =
            PyEliminationStrategies::_from_num(value);
        match converted {
            Some(strategy) => Ok(strategy),
            None => Err(PyValueError::new_err(format!(
                "Invalid elimination strategy value: {}", value
            )))
        }
    }
    #[classmethod]
    pub fn spawn_default<'_py>(_cls: Bound<'_py, pyo3::types::PyType>) -> Self {
        PyEliminationStrategies::DowdallScoring
    }
    #[classmethod]
    pub fn spawn_from_num<'_py>(
        _cls: Bound<'_py, pyo3::types::PyType>,
        value: u8
    ) -> PyResult<Self> {
        PyEliminationStrategies::new(value)
    }
}

define_stub_info_gatherer!(stub_info);
