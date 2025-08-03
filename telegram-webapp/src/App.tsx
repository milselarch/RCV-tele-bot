import axios from 'axios';
// import {ReactComponent as Logo} from './logo.svg';
import './App.scss';

import {
  MainButton, WebAppProvider, useThemeParams
} from '@vkruglikov/react-telegram-web-app';
import {useEffect, useState} from "react";
import {BACKEND_DEV_URL, BACKEND_PROD_URL} from "./config";
import ReactLoading from 'react-loading';

import {PollOptionsList} from "./PollOptionsList";
import {Poll} from "./poll";
import {applyThemeParams} from "./themeing";

// import { MainButton, useShowPopup } from '@vkruglikov/react-telegram-web-app';

const WITHHOLD_VOTE_VALUE = -1
const ABSTAIN_VOTE_VALUE = -2

const load_tele_headers = () => {
  let headers = window?.Telegram?.WebApp?.initData ?? '';

  if (headers === '') {
    console.log("Development mode");
    headers = window.location.search;
  }

  // console.log('INIT_DATA', initData)
  return headers
}

const get_backend_url = () => {
    if (!process.env.NODE_ENV || process.env.NODE_ENV === 'development') {
      return BACKEND_DEV_URL
  } else {
      return BACKEND_PROD_URL
  }
}

const fetch_poll = async (poll_id: number) => {
  const backend_url = get_backend_url()
  // console.log('BACKEND_URL', backend_url)
  const endpoint = `${backend_url}/fetch_poll`;
  // console.log('ENDPOINT', endpoint, poll_id)

  const request = axios.post(
    endpoint, {'poll_id': poll_id}, {
      headers: {'Content-Type': 'application/json'},
      timeout: 30 * 1000
    }
  )

  const response = await request
  // console.log('ENDPOINT RESPONSE', response)
  return response
}

const StatusLoader = ({
  loading, status
}: { loading: boolean, status: string | null }) => {
  if (status === null) {
    return null
  } else if (loading) {
    return (
      <div>
        <p>{status}</p>
        <ReactLoading
          type="bars" height={'10rem'} width={'10rem'}
          className="react-loading-hint"
        />
      </div>
    )
  } else {
    return (
      <div>
        <p>{status}</p>
      </div>
    )
  }
}

function App() {
  /*
  const showPopup = useShowPopup();
  const handleClick = () => showPopup({
    message: 'Hello, I am popup',
  });
  */

  // const [headers, set_headers] = useState('');
  const [has_credential, set_has_credential] = useState(false);
  const [poll, set_poll] = useState<Poll | null>(null)
  // reference telegram poll message that led to webapp inline prompt
  const [ref_info, set_ref_info] = useState<string>("")
  const [ref_hash, set_ref_hash] = useState<string>("")
  const [loading, set_loading] = useState(false)
  const [status, set_status] = useState<string>(null)

  const [vote_rankings, set_vote_rankings] = useState<Array<number>>([])
  const [withhold_final, set_withhold_final] = useState<boolean>(false);
  const [abstain_final, set_abstain_final] = useState<boolean>(false);

  const [_, themeParams] = useThemeParams();
  console.log('THEME PARAMS', themeParams)
  applyThemeParams(themeParams)

  const remove_ranking = (option_number: number) => {
    set_vote_rankings(vote_rankings.filter(
      rank => rank !== option_number
    ))
  }
  const add_ranking = (option_number: number) => {
    set_vote_rankings([...vote_rankings, option_number])
  }

  const set_special_vote_status = (
    cast_withhold: boolean, cast_abstain: boolean
  ) => {
    console.assert(!(cast_abstain && cast_withhold))
    // console.log('CAST', cast_withhold, cast_abstain)

    if (!withhold_final && !abstain_final) {
      // neither special vote has been cast
      set_withhold_final(cast_withhold)
      set_abstain_final(cast_abstain)
      return true
    } else {
      // unset both special votes
      set_withhold_final(false)
      set_abstain_final(false)
      return false
    }
  }

  const submit_vote_handler = () => {
    const final_vote_rankings = [...vote_rankings]
    console.assert(!(withhold_final && abstain_final))

    if (withhold_final) {
      final_vote_rankings.push(WITHHOLD_VOTE_VALUE)
    } else if (abstain_final) {
      final_vote_rankings.push(ABSTAIN_VOTE_VALUE)
    }
    // console.log('PAYLOAD', payload)
    window.Telegram.WebApp.sendData(JSON.stringify({
      'poll_id': poll.metadata.id, 'option_numbers': final_vote_rankings,
      'ref_info': ref_info, 'ref_hash': ref_hash
    }));
  }

  useEffect(() => {
    const headers = load_tele_headers()
    const has_credential = headers !== ''

    set_has_credential(has_credential);
    // set_headers(headers);

    if (has_credential) {
      axios.defaults.headers.common['telegram-data'] = headers;
    }

    const query_params = {};
    const params = new URLSearchParams(window.location.search);
    params.forEach((value, key) => { query_params[key] = value; });
    set_ref_info(query_params['ref_info'] ?? '')
    set_ref_hash(query_params['ref_hash'] ?? '')

    const poll_id = Number.parseInt(query_params['poll_id'])
    if (poll_id === null) {
      set_status('NO POLL ID SPECIFIED')
      throw 'NO POLL ID SPECIFIED'
    }

    set_loading(true)
    set_status('loading')

    fetch_poll(poll_id).then((response) => {
      if (response === null) { throw 'REQUEST FAILED' }
      console.log('AXIOS RESPONSE', response)
      const poll: Poll = response.data;
      set_status(null)
      set_poll(poll)

    }).catch((error) => {
      if (axios.isAxiosError(error)) {
        console.error('Axios error:', error);
        const status_code = error.response?.status;

        if (error.code === "ECONNABORTED") {
          set_status('Connection timed out')
        } else if (status_code === 401) {
          set_status('Unauthorized')
        } else if (status_code !== undefined) {
          set_status(`Server request failed (${status_code}) ;--;`)
        } else {
          set_status(`Server request failed ;--;`)
        }
      } else {
        console.error('Unexpected error:', error);
        set_status('Unexpected error')
      }

    }).finally(() => {
      set_loading(false)
    });
  }, [])

  return (
    <div className="App">
      <header className="App-header">
        <StatusLoader loading={loading} status={status}/>
        <PollOptionsList
          authenticated={has_credential} poll={poll}
          vote_rankings={vote_rankings}
          on_add_option={add_ranking}
          on_remove_option={remove_ranking}
          withhold_final={withhold_final}
          abstain_final={abstain_final}
          set_special_vote_status={set_special_vote_status}
        />
        <WebAppProvider>
          <MainButton
            text="Cast Vote" onClick={submit_vote_handler}
          />
        </WebAppProvider>
      </header>
    </div>
  )
}

export default App;

