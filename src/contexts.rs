use serde::{Serialize, Deserialize};

pub enum ContextTypes {
    PollCreation,
    VoteCreation
}

#[derive(Serialize, Deserialize, Debug)]
struct VoteCreation {
    context_type: String,
    poll_id: i32,
    votes: Vec<i32>,
}

pub struct ContextProcessor {
    templates: Vec<Box<Serialize + Deserialize>>
}


pub fn process_json(json_str: &str) {
    let result: Result<VoteCreation, serde_json::Error> = serde_json::from_str(json_str);

}