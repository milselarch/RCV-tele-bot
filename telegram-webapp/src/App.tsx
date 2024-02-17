import axios, { AxiosResponse } from 'axios';
// import {ReactComponent as Logo} from './logo.svg';
import './App.scss';
import { MainButton, WebAppProvider } from '@vkruglikov/react-telegram-web-app';
import {useEffect, useState} from "react";
import {BACKEND_DEV_URL, BACKEND_PROD_URL} from "./config";
import ReactLoading from 'react-loading';

import {PollOptionsList} from "./PollOptionsList";
import {Poll} from "./poll";

// import { MainButton, useShowPopup } from '@vkruglikov/react-telegram-web-app';

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
  const endpoint = `${backend_url}/fetch_poll`;
  console.log('ENDPOINT', endpoint, poll_id)

  const request = axios.post(
    endpoint, {'poll_id': poll_id}, {
      headers: {'Content-Type': 'application/json'},
      timeout: 5000 // time out after 5 seconds
    }
  )

  const response = await request
  console.log('ENDPOINT RESPONSE', response)
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
        <ReactLoading type="spin" height={'10rem'} width={'10rem'}/>
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

  const [headers, set_headers] = useState('');
  const [has_credential, set_has_credential] = useState(false);
  const [poll, set_poll] = useState<Poll | null>(null)
  const [vote_rankings, set_vote_rankings] = useState<Array<number>>([])
  const [loading, set_loading] = useState(false)
  const [status, set_status] = useState<string>(null)

  const remove_ranking = (option_index: number) => {
    set_vote_rankings(vote_rankings.filter(
      rank => rank !== option_index
    ))
  }
  const add_ranking = (option_index: number) => {
    set_vote_rankings([...vote_rankings, option_index])
  }

  const submit_vote_handler = () => {
    window.Telegram.WebApp.sendData(JSON.stringify({
      'poll_id': poll.poll_id, 'rankings': vote_rankings
    }));
  }

  useEffect(() => {
    const headers = load_tele_headers()
    const has_credential = headers !== ''

    set_has_credential(has_credential);
    set_headers(headers);

    if (has_credential) {
      axios.defaults.headers.common['telegram-data'] = headers;
    }

    const query_params = {};
    const params = new URLSearchParams(window.location.search);
    params.forEach((value, key) => { query_params[key] = value; });

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
        } else {
          set_status('Server request failed ;--;')
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

