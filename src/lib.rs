mod schema;
mod models;

use std::sync::Mutex;
use diesel::{Connection, MysqlConnection};
use pyo3::prelude::*;
use lazy_static::lazy_static;

lazy_static! {
    static ref DB_CONNECTION: Mutex<Option<MysqlConnection>> = Mutex::new(None);
}

#[pyfunction]
fn py_establish_connection(database_url: String) -> PyResult<String> {
    let connection = MysqlConnection::establish(&database_url)
        .map_err(|err| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            format!("Error connecting to {}: {}", database_url, err))
        )?;

    let mut db_conn = DB_CONNECTION.lock().unwrap();
    *db_conn = Some(connection);

    Ok("Connection established successfully".to_string())
}

pub fn establish_connection(database_url: String) -> MysqlConnection {
    MysqlConnection::establish(&database_url)
        .unwrap_or_else(|_| panic!("Error connecting to {}", database_url))
}

#[pyfunction]
fn determine_poll_winner(
    database_url: String
) -> PyResult<String> {
    let connection = &mut establish_connection(database_url);
    use crate::schema::vote_rankings::dsl::*;
    use crate::schema::poll_voters::dsl::*;

    let results = vote_rankings
        .inner_join(poll_voters.on(poll_voters::id.eq(vote_rankings::poll_voter)))
        .filter(poll_voters::poll.eq(poll_id))
        .order(vote_rankings::ranking.asc())
        .select((vote_rankings::id, vote_rankings::ranking))
        .load::<(i32, i32)>(connection)
        .map_err(|err| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            format!("Error querying votes: {}", err))
        )?;

    Ok("potato".to_string())
}
