use std::collections::HashMap;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use trie_rcv::{EliminationStrategies, RankedChoiceVoteTrie, RankedVote, VoteErrors};

#[pyclass]
struct VotesAggregator {
    raw_votes: HashMap<u64, Vec<i32>>,
    rcv: RankedChoiceVoteTrie
}
impl VotesAggregator {
    fn _flush_votes(&mut self) -> Result<bool, VoteErrors> {
        let mut raw_votes_inserted = false;
        for (_, raw_vote) in &self.raw_votes {
            let cast_result = RankedVote::from_vector(raw_vote)?;
            self.rcv.insert_vote(cast_result);
            raw_votes_inserted = true
        }
        self.raw_votes.clear();
        Ok(raw_votes_inserted)
    }
}
#[pymethods]
impl VotesAggregator {
    #[new]
    fn new() -> Self {
        VotesAggregator {
            raw_votes: Default::default(), rcv: Default::default()
        }
    }

    fn flush_votes(&mut self) -> PyResult<bool> {
        match self._flush_votes() {
            Ok(result) => Ok(result),
            Err(err) => Err(PyValueError::new_err(err.to_string()))
        }
    }

    fn insert_vote_ranking(&mut self, vote_id: u64, vote_ranking: i32) {
        let vote = self.raw_votes.entry(vote_id).or_insert(vec![]);
        vote.push(vote_ranking)
    }

    fn determine_winner(&mut self) -> PyResult<Option<u16>> {
        self.rcv.set_elimination_strategy(EliminationStrategies::DowdallScoring);
        let flush_result = self._flush_votes();
        if flush_result.is_err() {
            return Err(PyValueError::new_err(flush_result.unwrap_err().to_string()))
        }
        let winner = self.rcv.determine_winner();
        Ok(winner)
    }
}
