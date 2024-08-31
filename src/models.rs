use diesel::{Associations, Identifiable, Queryable};
use super::schema::{poll_voters, vote_rankings};

#[derive(Queryable, Identifiable, Associations)]
#[belongs_to(Poll)]
#[belongs_to(User)]
pub struct PollVoter {
    pub id: i32,
    pub poll: i32,
    pub user: i32,
    pub voted: bool,
}

#[derive(Queryable, Identifiable, Associations)]
#[belongs_to(PollVoter)]
#[belongs_to(PollOption)]
pub struct VoteRanking {
    pub id: i32,
    pub poll_voter: i32,
    pub option: Option<i32>,
    pub special_value: Option<i32>,
    pub ranking: i32,
}