use diesel::table;

// PollVoters table schema
table! {
    poll_voters (id) {
        id -> Integer,
        poll -> Integer,
        user -> Integer,
        voted -> Bool,
    }
}

// VoteRankings table schema
table! {
    vote_rankings (id) {
        id -> Integer,
        poll_voter -> Integer,
        option -> Nullable<Integer>,
        special_value -> Nullable<Integer>,
        ranking -> Integer,
    }
}