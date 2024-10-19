use std::fmt::{Debug, Display};
use serde::{Serialize, Deserialize, Serializer, Deserializer};
use trie_rcv::{RankedVote};

// https://stackoverflow.com/questions/42584368
pub trait Error: Debug + Display {
    fn description(&self) -> &str;
    fn cause(&self) -> Option<&dyn Error>;
    fn source(&self) -> Option<&(dyn Error + 'static)>;
}

#[derive(Debug)]
pub struct ContextError {
    description: String,
    cause: Option<Box<dyn Error>>,
}

const POLL_CREATION: &str = "POLL_CREATION";
const VOTE_CREATION: &str = "VOTE_CREATION";

#[derive(PartialEq, Eq, Hash)]
pub enum ContextTypes {
    PollCreation,
    VoteCreation
}
impl ContextTypes {
    fn from_str(context_type: &str) -> Result<ContextTypes, ContextError> {
        match context_type {
            POLL_CREATION => Ok(ContextTypes::PollCreation),
            VOTE_CREATION => Ok(ContextTypes::VoteCreation),
            _ => Err(ContextError {
                description: "INVALID CONTEXT TYPE".to_string(), cause: None
            })
        }
    }
    fn to_str(&self) -> String {
        match self {
            ContextTypes::PollCreation => POLL_CREATION.to_string(),
            ContextTypes::VoteCreation => VOTE_CREATION.to_string()
        }
    }
}

#[derive(Serialize, Deserialize, Debug)]
struct VoteCreationState {
    poll_id: u64,
    raw_vote: Vec<i32>,
}

pub struct VoteCreationContext {
    max_vote_options: u64
}
impl VoteCreationContext {
    pub fn new(max_options: u64) -> VoteCreationContext {
        VoteCreationContext {
            max_vote_options: max_options
        }
    }

    pub fn spawn(&self, poll_id: u64) -> String {
        let poll_state = VoteCreationState {
            poll_id, raw_vote: vec![]
        };
        serde_json::to_string(&poll_state).unwrap()
    }

    fn process(
        raw_context_state: &str
    ) -> Result<VoteCreationState, ContextError> {
         let poll_state_res: Result<
            VoteCreationState, serde_json::Error
        > = serde_json::from_str(raw_context_state);

        match poll_state_res {
            Ok(poll_state) => Ok(poll_state),
            Err(_) => Err(ContextError {
                description: "INVALID POLL STATE".to_string(),
                cause: None
            })
        }
    }

    pub fn transition(
        &self, raw_context_state: &str, option: i32
    ) -> Result<String, ContextError> {
        /*
        parses the raw context state into a PollCreationState
        and adds in the new option and returns the new state
        if its valid
        */
        let mut poll_state = VoteCreationContext::process(raw_context_state)?;
        poll_state.raw_vote.push(option);

        if poll_state.raw_vote.len() > self.max_vote_options as usize {
            return Err(ContextError {
                description: "Ranked vote is too long".to_string(),
                cause: None
            })
        }
        // check that the options form a valid ranked vote
        let cast_result = RankedVote::from_vector(&poll_state.raw_vote);
        if cast_result.is_err() {
            return Err(ContextError {
                description: "Ranked vote is invalid".to_string(),
                cause: None
            })
        }

        Ok(serde_json::to_string(&poll_state).unwrap())
    }
}

#[derive(Serialize, Deserialize, Debug)]
struct PollCreationState {
    poll_title: String,
    options: Vec<String>
}

pub struct PollCreationContext {
    max_options: u64
}
impl PollCreationContext {
    pub fn new(max_options: u64) -> PollCreationContext {
        PollCreationContext {
            max_options
        }
    }

    pub fn spawn(&self, poll_title: String) -> String {
        let poll_state = PollCreationState {
            poll_title, options: vec![]
        };
        serde_json::to_string(&poll_state).unwrap()
    }

    fn process(
        raw_context_state: &str
    ) -> Result<PollCreationState, ContextError> {
        let poll_state_res: Result<
            PollCreationState, serde_json::Error
        > = serde_json::from_str(raw_context_state);

        match poll_state_res {
            Ok(poll_state) => Ok(poll_state),
            Err(e) => Err(ContextError {
                description: "INVALID POLL STATE".to_string(),
                cause: None
            })
        }
    }

    pub fn transition(
        &self, raw_context_state: &str, option: String
    ) -> Result<String, ContextError> {
        /*
        parses the raw context state into a PollCreationState
        and adds in the new option and returns the new state
        if its valid
        */
        let mut poll_state = PollCreationContext::process(raw_context_state)?;
        poll_state.options.push(option);

        if poll_state.options.len() > self.max_options as usize {
            return Err(ContextError {
                description: "Too many options".to_string(),
                cause: None
            })
        }
        Ok(serde_json::to_string(&poll_state).unwrap())
    }
}


pub struct ContextProcessor {}
