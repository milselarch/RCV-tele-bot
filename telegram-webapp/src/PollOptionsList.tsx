import {Poll} from "./poll";

interface PollOption {
  readonly rank: number; // display rank number
  readonly option_number: number; // sort ID of option
  readonly description: string;
}

export const PollOptionsList = ({
  authenticated, poll, vote_rankings,
  on_add_option, on_remove_option, withhold_final, abstain_final,
  set_special_vote_status
}: {
  authenticated: boolean, poll: Poll | null,
  vote_rankings: Array<number>,
  on_add_option: (option_index: number) => void,
  on_remove_option: (option_index: number) => void,
  withhold_final: boolean, abstain_final: boolean,
  set_special_vote_status: (withhold: boolean, abstain: boolean) => boolean
}) => {
  console.log('AUTHENTICATED', authenticated)
  if (!authenticated || (poll === null)) {
    return null
  }

  // map poll option sort IDs to their indexes within the poll
  const option_nos_map = {}
  for (let k = 0; k < poll.poll_options.length; k++) {
    option_nos_map[poll.option_numbers[k]] = k;
  }

  const unused_poll_options: Array<PollOption> = []
  for (let k = 0; k < poll.poll_options.length; k++) {
    const option_number = poll.option_numbers[k]

    if (vote_rankings.indexOf(option_number) === -1) {
      unused_poll_options.push({
        description: poll.poll_options[k],
        rank: poll.option_numbers[k],
        option_number: poll.option_numbers[k]
      })
    }
  }

  const used_poll_options: Array<
    PollOption
  > = vote_rankings.map((option_number: number, index: number) => {
    const option_index = option_nos_map[option_number]
    const description = poll.poll_options[option_index]
    return {
      description: description, rank: index+1,
      option_number: option_number
    }
  })

  // console.log('POLL', poll)
  const poll_option_renderer = (option: PollOption) => (
    <div
      key={option.option_number} className="poll-option"
      onClick={() => on_add_option(option.option_number)}
    >
      <p className="no-select index">{option.rank}.</p>
      <p className="no-select option">{option.description}</p>
    </div>
  )
  const voted_option_renderer = (option: PollOption) => (
    <div
      key={option.option_number} className="poll-option"
      onClick={() => on_remove_option(option.option_number)}
    >
      <p className="no-select index">#{option.rank}</p>
      <p className="no-select option">{option.description}</p>
    </div>
  )

  const unused_poll_items = unused_poll_options.map(poll_option_renderer);
  const used_poll_items = used_poll_options.map(voted_option_renderer);

  return (
    <div className="poll-container">
      <div className="title">
        <p className="no-select">
          {poll?.poll_question}
        </p>
      </div>

      <div className="buffer"></div>

      <div className="poll-selector">
        <div className="poll-options selected">
          { used_poll_items.map((render_item) => (render_item)) }

          {
            (withhold_final || abstain_final) &&
            <div className="special-votes">
            { withhold_final &&
              <div className="poll-option" onClick={() => set_special_vote_status(false, false)}>
                <p className="no-select option">&lt;&lt;&lt; withhold vote &gt;&gt;&gt;</p>
              </div>
            } { abstain_final &&
              <div className="poll-option" onClick={() => set_special_vote_status(false, false)}>
                <p className="no-select option">&lt;&lt;&lt; abstain vote &gt;&gt;&gt;</p>
              </div>
            }
            </div>
          }
        </div>

        <div className="poll-options">
          {unused_poll_items.map((render_item) => (render_item)) }

          {
            (!withhold_final && !abstain_final) &&
            <div className="special-votes">
              <div className="poll-option" onClick={() => set_special_vote_status(true, false)}>
                <p className="no-select option">withhold</p>
              </div>
              <div className="poll-option" onClick={() => set_special_vote_status(false, true)}>
                <p className="no-select option">abstain</p>
              </div>
            </div>
          }
        </div>
      </div>

      <span className="mono"> [{poll?.poll_id}] </span>
    </div>
  )
}
