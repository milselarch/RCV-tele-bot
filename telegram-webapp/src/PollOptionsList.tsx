import {Poll} from "./poll";

interface PollOption {
  poll_index: number;
  description: string;
}

export const PollOptionsList = ({
  authenticated, poll, vote_rankings,
  on_add_option, on_remove_option,
}: {
  authenticated: boolean, poll: Poll | null,
  vote_rankings: Array<number>,
  on_add_option: (option_index: number) => void,
  on_remove_option: (option_index: number) => void,
}) => {
  console.log('AUTHENTICATED', authenticated)
  if (!authenticated || (poll === null)) {
    return null
  }

  const unused_poll_options: Array<PollOption> = []
  for (let k = 0; k < poll.poll_options.length; k++) {
    if (vote_rankings.indexOf(k) === -1) {
      unused_poll_options.push({
        description: poll.poll_options[k], poll_index: k
      })
    }
  }

  const used_poll_options: Array<
    PollOption
  > = vote_rankings.map((option_index: number) => {
    return {
      description: poll.poll_options[option_index], poll_index: option_index
    }
  })

  console.log('POLL', poll)
  const poll_option_renderer = (option: PollOption, index: number) => (
    <div
      key={index} className="poll-option"
      onClick={() => on_add_option(option.poll_index)}
    >
      <p className="no-select index">{index + 1}.</p>
      <p className="no-select option">{option.description}</p>
    </div>
  )
  const voted_option_renderer = (option: PollOption, index: number) => (
    <div
      key={index} className="poll-option"
      onClick={() => on_remove_option(option.poll_index)}
    >
      <p className="no-select index">#{index + 1}</p>
      <p className="no-select option">{option.description}</p>
    </div>
  )

  const unused_poll_items = unused_poll_options.map(poll_option_renderer);
  const used_poll_items = used_poll_options.map(voted_option_renderer);

  return (
    <div className="poll-container">
      <div className="title">
        <p className="no-select">
          {poll?.poll_question} &nbsp;
        </p>
      </div>

      <div className="poll-selector">
        <div className="buffer"></div>
        <div className="poll-options">
          {used_poll_items.map((render_item) => (render_item))}
        </div>
        <br/>
        <div className="poll-options">
          {unused_poll_items.map((render_item) => (render_item))}
        </div>
      </div>

      <span className="mono"> #{poll?.poll_id} </span>
    </div>
  )
}
