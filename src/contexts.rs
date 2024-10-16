use std::collections::HashMap;
use std::fmt::{Debug, Display};
use serde::{Serialize, Deserialize, Serializer, Deserializer};

// https://stackoverflow.com/questions/42584368
pub trait Error: Debug + Display {
    fn description(&self) -> &str;
    fn cause(&self) -> Option<&dyn Error>;
    fn source(&self) -> Option<&(dyn Error + 'static)>;
}

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
    poll_id: i32,
    raw_votes: Vec<i32>,
}
#[derive(Serialize, Deserialize, Debug)]
struct PollCreationState {
    poll_title: String,
    options: Vec<String>
}

pub trait Context{
    fn transition(
        &self, raw_context_state: &str
    ) -> Result<String, ContextError>;
}

pub struct PollCreationContext {}
impl Context for PollCreationContext {
    fn transition(
        &self, raw_context_state: &str
    ) -> Result<String, ContextError> {
        let poll_state: Result<
            PollCreationState, serde_json::Error
        > = serde_json::from_str(raw_context_state);

        match poll_state {
            Ok(poll_state) => {
                // Do something with the poll state
                Ok("SUCCESS".to_string())
            }
            Err(_) => Err(ContextError {
                description: "INVALID POLL STATE".to_string(), cause: None
            })
        }
    }
}


pub struct ContextProcessor {}
