export interface PollMetadata {
  readonly id: number,
  readonly question: string,
  readonly num_voters: number
  readonly num_votes: number

  readonly open_registration: boolean
  readonly closed: boolean
}

export interface Poll {
  readonly poll_options: Array<string>,
  readonly option_numbers: Array<number>,
  readonly metadata: PollMetadata
}
