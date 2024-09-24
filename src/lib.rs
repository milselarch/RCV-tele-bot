use std::collections::HashMap;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3_stub_gen::{
    derive::gen_stub_pymethods, derive::gen_stub_pyclass,
    define_stub_info_gatherer
};

use trie_rcv::{
    EliminationStrategies, RankedChoiceVoteTrie, RankedVote, SpecialVotes, VoteErrors
};

const WITHOLD_VOTE_VAL: i32 = SpecialVotes::WITHHOLD.to_int();


#[gen_stub_pyclass]
#[pyclass]
pub struct VotesAggregator {
    raw_votes_cache: HashMap<u64, Vec<i32>>,
    rcv: RankedChoiceVoteTrie
}
impl VotesAggregator {
    fn _flush_votes(&mut self) -> Result<bool, VoteErrors> {
        // convert raw votes into RankedVotes into the trie
        let mut raw_votes_inserted = false;
        for (_, raw_vote) in &self.raw_votes_cache {
            let cast_result = RankedVote::from_vector(raw_vote)?;
            self.rcv.insert_vote(cast_result);
            raw_votes_inserted = true
        }
        self.raw_votes_cache.clear();
        Ok(raw_votes_inserted)
    }
}
#[gen_stub_pymethods]
#[pymethods]
impl VotesAggregator {
    #[new]
    fn new() -> Self {
        VotesAggregator {
            raw_votes_cache: Default::default(), rcv: Default::default()
        }
    }

    fn flush_votes(&mut self) -> PyResult<bool> {
        match self._flush_votes() {
            Ok(result) => Ok(result),
            Err(err) => Err(PyValueError::new_err(err.to_string()))
        }
    }

    fn get_num_votes(&self) -> PyResult<u64> {
        // return the total number of votes cast
        Ok(
            self.rcv.get_num_votes() +
            self.raw_votes_cache.len() as u64
        )
    }

    fn insert_vote_ranking(&mut self, vote_id: u64, vote_ranking: i32) {
        let vote = self.raw_votes_cache.entry(vote_id).or_insert(vec![]);
        vote.push(vote_ranking)
    }

    fn insert_empty_votes(&mut self, num_votes: u64) -> PyResult<bool> {
        // insert withhold votes to represent registered voters
        // who did not vote in the poll
        for _ in 0..num_votes {
            let withhold_vote: RankedVote = RankedVote::from_vector(
                &vec![WITHOLD_VOTE_VAL]
            ).unwrap();

            self.rcv.insert_vote(withhold_vote)
        }
        Ok(true)
    }

    fn determine_winner(&mut self) -> PyResult<Option<u16>> {
        // TODO: implement elimination strategy selection
        self.rcv.set_elimination_strategy(EliminationStrategies::DowdallScoring);
        let flush_result = self._flush_votes();
        if flush_result.is_err() {
            return Err(PyValueError::new_err(flush_result.unwrap_err().to_string()))
        }
        let winner = self.rcv.determine_winner();
        Ok(winner)
    }
}

#[pymodule]
fn ranked_choice_vote(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<VotesAggregator>()?;
    Ok(())
}

define_stub_info_gatherer!(stub_info);
